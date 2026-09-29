# scripts/backup/test-nas-sync.ps1
#
# Tests for the weekly NAS job and the one-time archive copy, run against LOCAL
# stand-in directories only - no SMB, no NAS, no Docker. Every path it writes is
# under a fresh directory in %TEMP% that it removes at the end (-Keep leaves it).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup\test-nas-sync.ps1
#
# -Script / -Lib / -CopyScript point the tests at other copies (the base RED run
# swaps in the 0fb1c0c backup-to-nas.ps1; the mutation runs swap in mutants). The
# copy script and the job always run from a throw-away directory that holds the
# -Lib under test next to them, because each dot-sources the lib beside ITSELF.
# Exit = number of failed checks.
#
# Sections:
#   S  the job's wiring, read from its AST/text;
#   R  the pure rules in nas-sync-lib.ps1 (archive root, normalisation);
#   M  the archive pass (Invoke-NasArchivePass) and the slot mirror (robocopy) on
#      stand-in trees: new files copied verified, complete copies never replaced,
#      incomplete ones (robocopy's 1980 stamp, a leftover temp) repaired, content
#      differences reported with which side to trust, top-level sources excluded;
#   C  copy-archives-to-nas.ps1 run as a process against a stand-in archive root;
#   G  the exclusion rule holds for THIS repo: every git-tracked file under backup/
#      is top-level;
#   J  backup-to-nas.ps1 RUN END TO END in a child PowerShell, inside a throw-away
#      project tree, with net.exe / Resolve-DnsName / the alerter stubbed and the
#      NAS share \\192.0.2.77\backups (TEST-NET-1: not routable) MAPPED onto a local
#      directory: robocopy.exe and the file cmdlets the lib uses (Get-ChildItem,
#      Get-Item, Test-Path, Get-FileHash, Copy-Item, Move-Item, New-Item) see the
#      local directory; any other UNC path is refused by the stubs and recorded as
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
function StatusOf($results, [string]$rel) { (@($results) | Where-Object { $_.Rel -eq $rel } | Select-Object -First 1) }

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
Check 'S3 exactly one robocopy call - the slot mirror, splatting the lib-built set' (($callText.Count -eq 1) -and ($callText -contains '& robocopy.exe @mirrorArgs')) ($callText -join ' | ')
Check 'S4 the mirror args come from Get-NasSlotMirrorArgs over ./backups' ($text -match '\$mirrorArgs = Get-NasSlotMirrorArgs -Source \$backupSrc -Destination \$nasDest')
Check 'S5 the archive pass is Invoke-NasArchivePass over ./backup to the resolved archive root' (($text -match "\`$archiveSrc = Join-Path \`$projectRoot 'backup'") -and ($text -match 'Invoke-NasArchivePass -Source \$archiveSrc -Destination \$archiveDest') -and ($text -match 'Resolve-NasArchiveRoot -NasUncRoot \$NasUncRoot'))
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
$ma = Try-Call { Get-NasSlotMirrorArgs -Source 'C:\s' -Destination 'C:\d' -LogFile 'C:\l.log' }
Check 'R7 slot args unchanged from 0fb1c0c: /MIR /R:3 /W:5 /Z /MT:8 /XJ /LOG+ /NDL' (($ma -join ' ') -eq 'C:\s C:\d /MIR /R:3 /W:5 /Z /MT:8 /XJ /LOG+:C:\l.log /NDL') ($ma -join ' ')
$md = Try-Call { Get-NasSlotMirrorArgs -Source 'C:\s' -Destination 'C:\d' -DryRun }
Check 'R8 -DryRun adds /L to the mirror' ($md -contains '/L')
Check 'R9 ISO week parity: 2026-09-27 (week 39) -> slot-B, 2026-10-04 (week 40) -> slot-A' (((Get-NasSlotName -Date ([datetime]'2026-09-27')) -eq 'slot-B') -and ((Get-NasSlotName -Date ([datetime]'2026-10-04')) -eq 'slot-A'))
Check 'R10 the robocopy archive argument set of attempts 1-2 is gone (clean replacement)' (-not (Get-Command Get-NasArchiveCopyArgs -ErrorAction SilentlyContinue))
Check 'R11 robocopy''s unfinished-copy stamp (1980-01-02) is INCOMPLETE; 1980-01-03 and 2026 are not' ((Test-NasIncompleteStamp ([datetime]'1980-01-02')) -and (Test-NasIncompleteStamp ([datetime]'1980-01-01')) -and -not (Test-NasIncompleteStamp ([datetime]'1980-01-03')) -and -not (Test-NasIncompleteStamp ([datetime]'2026-09-13')))

# ---------------------------------------------------------------- stand-in trees
$root = Join-Path ([System.IO.Path]::GetTempPath()) ("cf-nas-test-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$proj = Join-Path $root 'proj'
$backups = Join-Path $proj 'backups'
$backup = Join-Path $proj 'backup'
$nasPortal = Join-Path $root 'nas\backups\ai-stack\portal'
$log = Join-Path $root 'robocopy.log'
New-Item -ItemType Directory -Force -Path "$backups\openwebui", "$backup\orphan-volumes-2026-09-13", "$backup\nas-archive-may2025-owui", "$backup\models", "$backup\extra" | Out-Null
$ps = (Get-Process -Id $PID).Path
try {
  $rnd = New-Object System.Random 7
  function Blob([string]$p, [int]$n) { $b = New-Object byte[] $n; $rnd.NextBytes($b); [System.IO.File]::WriteAllBytes($p, $b) }
  function Stamp([string]$p, [datetime]$t) { [System.IO.File]::SetLastWriteTimeUtc($p, $t) }
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
  Blob "$backup\extra\thing.tar" 20000
  Set-Content -LiteralPath "$backup\extra\thing.tar.sha256" -Value ((Sha256Of "$backup\extra\thing.tar") + '  thing.tar') -Encoding ascii
  # top-level = the git-tracked sidecar sources; never archived
  Set-Content -LiteralPath "$backup\generic-tar-backup.sh" -Value 'echo stand-in' -Encoding ascii
  Set-Content -LiteralPath "$backup\README.md" -Value 'sidecar readme' -Encoding ascii
  $srcAll = TreeHashes $backup
  $srcArch = @{}; foreach ($k in $srcAll.Keys) { if ($k.Substring(1).Contains('\')) { $srcArch[$k] = $srcAll[$k] } }
  $backupsBefore = TreeHashes $backups

  Write-Host "== M: archive pass + slot mirror on stand-in trees"
  $archive = Resolve-NasArchiveRoot -NasUncRoot $nasPortal
  Check 'M0 stand-in archive root = sibling of portal' ($archive -eq (Join-Path $root 'nas\backups\ai-stack\archive')) $archive
  $slotA = Join-Path $nasPortal 'slot-A'; $slotB = Join-Path $nasPortal 'slot-B'

  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotA -LogFile $log)
  Check 'M1 slot-A mirror ok' ($rc -lt 8) "rc=$rc"
  $res = @(Invoke-NasArchivePass -Source $backup -Destination $archive)
  Check 'M2 first pass: every archive COPIED (9 files), nothing else' ((@($res | Where-Object { $_.Status -eq 'COPIED' }).Count -eq 9) -and ($res.Count -eq 9)) (($res | ForEach-Object { "$($_.Status):$($_.Rel)" }) -join ' ')
  $arcAfterFirst = TreeHashes $archive
  Check 'M3 the NAS archive = the subdirectory files of ./backup exactly (hashes)' (SameTree $srcArch $arcAfterFirst) "src=$($srcArch.Count) nas=$($arcAfterFirst.Count)"
  Check 'M3b the top-level sidecar sources are NOT archived' (-not (Test-Path -LiteralPath "$archive\generic-tar-backup.sh") -and -not (Test-Path -LiteralPath "$archive\README.md"))
  Check 'M3c timestamps preserved, no *.cf-partial left' (((Get-Item -LiteralPath "$archive\extra\thing.tar").LastWriteTimeUtc -eq (Get-Item -LiteralPath "$backup\extra\thing.tar").LastWriteTimeUtc) -and -not (Get-ChildItem -LiteralPath $archive -Recurse -Filter '*.cf-partial'))
  Check 'M4 archives did not land in any slot' (-not (Get-ChildItem -LiteralPath $nasPortal -Recurse -File | Where-Object { $_.Name -like '*.tar' }))

  Set-Content -LiteralPath "$slotA\stray.txt" -Value 'x' -Encoding ascii
  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotA -LogFile $log)
  Check 'M5 slot mirror still purges extras in its slot' ((-not (Test-Path -LiteralPath "$slotA\stray.txt")) -and ($rc -band 2)) "rc=$rc"
  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotB -LogFile $log)
  $res = @(Invoke-NasArchivePass -Source $backup -Destination $archive)
  Check 'M6 an unchanged second pass: all PRESENT (fast path, not hashed), NAS unchanged' ((@($res | Where-Object { $_.Status -eq 'PRESENT' -and $_.Detail -like '*not hashed*' }).Count -eq 9) -and (SameTree $arcAfterFirst (TreeHashes $archive)))
  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotA -LogFile $log)
  Check 'M8 archive tree byte-identical after mirrors of both slots' (SameTree $arcAfterFirst (TreeHashes $archive))

  Set-Content -LiteralPath "$archive\operator-note.txt" -Value 'kept' -Encoding ascii
  $gone = Join-Path $root 'moved-out.tar'
  Move-Item -LiteralPath "$backup\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar" -Destination $gone
  $res = @(Invoke-NasArchivePass -Source $backup -Destination $archive)
  Move-Item -LiteralPath $gone -Destination "$backup\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar"
  Check 'M9 a file deleted locally stays on the NAS' (Test-Path -LiteralPath "$archive\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar")
  Check 'M10 a NAS-side extra stays' (Test-Path -LiteralPath "$archive\operator-note.txt")
  Remove-Item -LiteralPath "$archive\operator-note.txt"
  $arcState = TreeHashes $archive

  # F1: a CHANGED or TRUNCATED local file never replaces a complete NAS copy - it is a MISMATCH
  function Save-Local([string]$p) { @{ P = $p; B = [System.IO.File]::ReadAllBytes($p); T = (Get-Item -LiteralPath $p).LastWriteTimeUtc } }
  function Restore-Local($s) { [System.IO.File]::WriteAllBytes($s.P, $s.B); Stamp $s.P $s.T }
  $sv1 = Save-Local "$backup\models\models-export.json"; $sv2 = Save-Local "$backup\extra\thing.tar"; $sv3 = Save-Local "$backup\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar"
  Add-Content -LiteralPath "$backup\models\models-export.json" -Value 'more' -Encoding ascii
  [System.IO.File]::WriteAllBytes("$backup\extra\thing.tar", [byte[]]@())
  # same size, different content, different timestamp (the fast path must not skip it)
  $b3 = [byte[]]$sv3.B.Clone(); $b3[100] = $b3[100] -bxor 0xFF; [System.IO.File]::WriteAllBytes($sv3.P, $b3); Stamp $sv3.P ($sv3.T.AddDays(3))
  $res = @(Invoke-NasArchivePass -Source $backup -Destination $archive)
  Check 'M11 a changed local file (bigger) is MISMATCH, NAS copy untouched' (((StatusOf $res 'models\models-export.json').Status -eq 'MISMATCH'))
  $t2 = StatusOf $res 'extra\thing.tar'
  Check 'M12 a local file truncated to 0 bytes is MISMATCH and the verdict is: trust the NAS (it matches thing.tar.sha256)' (($t2.Status -eq 'MISMATCH') -and ($t2.Trust -like 'NAS *')) "$($t2.Status) / $($t2.Trust)"
  Check 'M12b same size, different content, different timestamp is MISMATCH (verdict UNKNOWN: no recorded checksum)' (((StatusOf $res 'orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar').Status -eq 'MISMATCH') -and ((StatusOf $res 'orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar').Trust -like 'UNKNOWN*'))
  Check 'M13 the whole NAS archive tree is unchanged by that pass' (SameTree $arcState (TreeHashes $archive))
  Restore-Local $sv1; Restore-Local $sv2; Restore-Local $sv3
  # timestamp-only drift: same content, other mtime -> hashed, PRESENT, no error
  Stamp $sv2.P ($sv2.T.AddHours(-5))
  $res = @(Invoke-NasArchivePass -Source $backup -Destination $archive)
  $t2 = StatusOf $res 'extra\thing.tar'
  Check 'M14 timestamp-only drift is hashed and PRESENT (not an error, not a copy)' (($t2.Status -eq 'PRESENT') -and ($t2.Detail -like 'sha256=*')) "$($t2.Status) $($t2.Detail)"
  Stamp $sv2.P $sv2.T
  # the NAS copy damaged, local intact and vouched for by its checksum
  $nt = "$archive\extra\thing.tar"; $ntB = [System.IO.File]::ReadAllBytes($nt); $ntT = (Get-Item -LiteralPath $nt).LastWriteTimeUtc
  [System.IO.File]::WriteAllBytes($nt, [byte[]]@(1, 2, 3)); Stamp $nt $ntT
  $res = @(Invoke-NasArchivePass -Source $backup -Destination $archive)
  $t2 = StatusOf $res 'extra\thing.tar'
  Check 'M15 a damaged complete NAS copy is MISMATCH, verdict LOCAL, and is NOT overwritten' (($t2.Status -eq 'MISMATCH') -and ($t2.Trust -like 'LOCAL *') -and ((Get-Item -LiteralPath $nt).Length -eq 3)) "$($t2.Status) / $($t2.Trust)"
  [System.IO.File]::WriteAllBytes($nt, $ntB); Stamp $nt $ntT

  # F4: interrupted copies are REPAIRED on the next pass
  $ip = "$archive\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar"
  $junk = New-Object byte[] 3000000; [System.IO.File]::WriteAllBytes($ip, $junk); Stamp $ip ([datetime]'1980-01-02')
  $lp = "$archive\nas-archive-may2025-owui\open-webui.tar"; Remove-Item -LiteralPath $lp
  Set-Content -LiteralPath "$lp.cf-partial" -Value 'half' -Encoding ascii
  $res = @(Invoke-NasArchivePass -Source $backup -Destination $archive)
  Check 'M16 a full-length, wrong-content NAS file with robocopy''s 1980 stamp is REPAIRED (content and timestamp now local)' (((StatusOf $res 'orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar').Status -eq 'REPAIRED') -and ((Sha256Of $ip) -eq $srcArch['\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar']) -and ((Get-Item -LiteralPath $ip).LastWriteTimeUtc.Year -gt 1980))
  Check 'M17 a leftover .cf-partial beside a missing file is overwritten and the file COPIED, temp gone' (((StatusOf $res 'nas-archive-may2025-owui\open-webui.tar').Status -eq 'COPIED') -and ((Sha256Of $lp) -eq $srcArch['\nas-archive-may2025-owui\open-webui.tar']) -and -not (Test-Path -LiteralPath "$lp.cf-partial"))
  $res = @(Invoke-NasArchivePass -Source $backup -Destination $archive)
  Check 'M18 the pass after that: all PRESENT, NAS = local' ((@($res | Where-Object { $_.Status -ne 'PRESENT' }).Count -eq 0) -and (SameTree $srcArch (TreeHashes $archive)))
  Blob "$backup\models\new-export.json" 500
  $before = TreeHashes $archive
  $res = @(Invoke-NasArchivePass -Source $backup -Destination $archive -DryRun)
  Check 'M19 -DryRun: WOULD-COPY for a new file, nothing written' (((StatusOf $res 'models\new-export.json').Status -eq 'WOULD-COPY') -and (SameTree $before (TreeHashes $archive)))
  Remove-Item -LiteralPath "$backup\models\new-export.json"

  Write-Host "== P: a slot spelling never reaches the archive pass (local stand-in)"
  foreach ($sp in $SlotSpellings) {
    $threw = $false; try { Resolve-NasArchiveRoot -NasUncRoot $nasPortal -NasArchiveRoot ((Join-Path $root 'nas\backups\ai-stack') + $sp) | Out-Null } catch { $threw = $true }
    Check "P1 stand-in spelling refused [...ai-stack$sp]" $threw
  }

  # ------------------------------------------------------------ copy script
  Write-Host "== C: copy-archives-to-nas.ps1 (run beside the -Lib under test)"
  $cdir = Join-Path $root 'copyrun\scripts\backup'
  New-Item -ItemType Directory -Force -Path $cdir | Out-Null
  Copy-Item -LiteralPath $CopyScript -Destination "$cdir\copy-archives-to-nas.ps1"
  Copy-Item -LiteralPath $Lib -Destination "$cdir\nas-sync-lib.ps1"
  $CopyRun = "$cdir\copy-archives-to-nas.ps1"
  $dest = Join-Path $root 'nas2\backups\ai-stack\archive'
  function RunCopyTo([string]$d, [string[]]$extra, [string]$src = $backup) {
    $ErrorActionPreference = 'Continue'   # the child's stderr must not abort the test
    $out = & $ps -NoProfile -ExecutionPolicy Bypass -File $CopyRun -Destination $d -Source $src @extra 2>&1 | Out-String
    return @{ Out = $out; Rc = $LASTEXITCODE }
  }
  $r = RunCopyTo $dest @('-VerifyOnly')
  Check 'C1 -VerifyOnly on an empty NAS: ABSENT, exit 1, nothing written' (($r.Rc -eq 1) -and ($r.Out -match 'ABSENT') -and -not (Test-Path -LiteralPath $dest)) "rc=$($r.Rc)"
  $r = RunCopyTo $dest @()
  $verified = ([regex]::Matches($r.Out, 'VERIFIED \(copied\)')).Count
  Check 'C2 first run copies all 6 files of the two dirs, each VERIFIED, exit 0' (($r.Rc -eq 0) -and ($verified -eq 6)) "rc=$($r.Rc) verified=$verified`n$($r.Out)"
  Check 'C3 models\ is not in the default set' (-not (Test-Path -LiteralPath "$dest\models"))
  Check 'C4 no *.cf-partial left behind' (-not (Get-ChildItem -LiteralPath $dest -Recurse -Filter '*.cf-partial'))
  $f1 = Get-Item -LiteralPath "$dest\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar"
  Check 'C5 timestamp preserved' ($f1.LastWriteTimeUtc -eq (Get-Item -LiteralPath "$backup\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar").LastWriteTimeUtc)
  $destState = TreeHashes $dest
  $r = RunCopyTo $dest @()
  Check 'C6 second run: all VERIFIED (already present), exit 0, NAS unchanged' (($r.Rc -eq 0) -and (([regex]::Matches($r.Out, 'already present')).Count -eq 6) -and (SameTree $destState (TreeHashes $dest))) "rc=$($r.Rc)"
  $r = RunCopyTo $dest @('-VerifyOnly')
  Check 'C7 -VerifyOnly after the copy: exit 0' ($r.Rc -eq 0) "rc=$($r.Rc)"
  $res = @(Invoke-NasArchivePass -Source $backup -Destination $dest)
  $copiedNow = @($res | Where-Object { $_.Status -eq 'COPIED' } | ForEach-Object { $_.Rel } | Sort-Object)
  Check 'C8 the weekly pass after the one-time copy copies only what it did not cover (models\, extra\) and finds the 6 PRESENT unhashed' ((($copiedNow -join ',') -eq 'extra\thing.tar,extra\thing.tar.sha256,models\models-export.json') -and (@($res | Where-Object { $_.Status -eq 'PRESENT' -and $_.Detail -like '*not hashed*' }).Count -eq 6)) ($copiedNow -join ',')
  $res = @(Invoke-NasArchivePass -Source $backup -Destination $dest)
  Check 'C9 ... and the run after that: all PRESENT - the next scheduled run keeps them' (@($res | Where-Object { $_.Status -ne 'PRESENT' }).Count -eq 0)

  $victim = "$dest\nas-archive-may2025-owui\open-webui.tar"
  $fs = [System.IO.File]::Open($victim, 'Open', 'ReadWrite'); $fs.WriteByte(0x41); $fs.Close()
  $tampered = Sha256Of $victim
  $r = RunCopyTo $dest @()
  Check 'C10 a differing complete NAS file is MISMATCH, exit 1, left untouched, verdict LOCAL (SHA256SUMS)' (($r.Rc -eq 1) -and ($r.Out -match 'MISMATCH\s+nas-archive-may2025-owui\\open-webui\.tar.*Trust: LOCAL') -and ((Sha256Of $victim) -eq $tampered)) "rc=$($r.Rc)"

  $dest3 = Join-Path $root 'nas3\archive'
  $rot = "$backup\nas-archive-may2025-owui\may2025-chats-export.json"
  $svr = Save-Local $rot
  $b = [byte[]]$svr.B.Clone(); $b[0] = $b[0] -bxor 0xFF; [System.IO.File]::WriteAllBytes($rot, $b)
  $r = RunCopyTo $dest3 @()
  Restore-Local $svr
  Check 'C11 a local file that no longer matches SHA256SUMS is FAIL LOCAL and not copied' (($r.Rc -eq 1) -and ($r.Out -match 'FAIL LOCAL\s+nas-archive-may2025-owui\\may2025-chats-export\.json') -and -not (Test-Path -LiteralPath "$dest3\nas-archive-may2025-owui\may2025-chats-export.json")) "rc=$($r.Rc)"

  $dest4 = Join-Path $root 'nas4\archive'
  New-Item -ItemType Directory -Force -Path "$dest4\orphan-volumes-2026-09-13" | Out-Null
  Set-Content -LiteralPath "$dest4\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar.cf-partial" -Value 'half' -Encoding ascii
  $r = RunCopyTo $dest4 @('-Dirs', 'orphan-volumes-2026-09-13')
  Check 'C12 a leftover .cf-partial is overwritten and the file lands VERIFIED' (($r.Rc -eq 0) -and ((Sha256Of "$dest4\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar") -eq (Sha256Of "$backup\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar")) -and -not (Test-Path -LiteralPath "$dest4\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar.cf-partial")) "rc=$($r.Rc)"
  $v4 = "$dest4\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar"
  [System.IO.File]::WriteAllBytes($v4, (New-Object byte[] 500000)); Stamp $v4 ([datetime]'1980-01-02')
  $r = RunCopyTo $dest4 @('-Dirs', 'orphan-volumes-2026-09-13', '-VerifyOnly')
  Check 'C12b -VerifyOnly reports a 1980-stamped NAS copy INCOMPLETE (exit 1) and writes nothing' (($r.Rc -eq 1) -and ($r.Out -match 'INCOMPLETE\s+orphan-volumes-2026-09-13\\ai-stack_llm-gateway-db-data\.tar') -and ((Get-Item -LiteralPath $v4).LastWriteTimeUtc.Year -eq 1980))
  $r = RunCopyTo $dest4 @('-Dirs', 'orphan-volumes-2026-09-13')
  Check 'C12c ... and the copy REPAIRS it (VERIFIED (repaired), exit 0, content = local)' (($r.Rc -eq 0) -and ($r.Out -match 'VERIFIED \(repaired') -and ((Sha256Of $v4) -eq (Sha256Of "$backup\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar")))

  foreach ($sp in (@('\portal\slot-A\x') + $SlotSpellings)) {
    $badDest = (Join-Path $root 'nas5\backups\ai-stack') + $sp
    $ErrorActionPreference = 'Continue'
    $null = & $ps -NoProfile -ExecutionPolicy Bypass -File $CopyRun -Destination $badDest -Source $backup 2>&1 | Out-String
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    Check "C13 destination refused (exit 2) [...ai-stack$sp]" ($code -eq 2) "rc=$code"
  }
  Check 'C13z no refused spelling wrote anything' (-not (Test-Path -LiteralPath (Join-Path $root 'nas5')))

  $ghostSrc = Join-Path $root 'ghostsrc'
  New-Item -ItemType Directory -Force -Path "$ghostSrc\g" | Out-Null
  Blob "$ghostSrc\g\real.tar" 1000
  [System.IO.File]::WriteAllLines("$ghostSrc\g\SHA256SUMS", [string[]]@("$(Sha256Of "$ghostSrc\g\real.tar") *real.tar", (('0' * 64) + ' *ghost.tar')))
  $r = RunCopyTo (Join-Path $root 'nas6\archive') @('-Dirs', 'g') $ghostSrc
  Check 'C14 a SHA256SUMS entry with no local file is MISSING LOCAL, exit 1 (the present file still VERIFIED)' (($r.Rc -eq 1) -and ($r.Out -match 'MISSING LOCAL\s+g\\ghost\.tar') -and ($r.Out -match 'real\.tar.*VERIFIED')) "rc=$($r.Rc)"

  # ------------------------------------------------------------ G: the exclusion rule vs this repo
  Write-Host "== G: every git-tracked file under backup/ is top-level (so position-based exclusion covers them)"
  $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
  $ErrorActionPreference = 'Continue'
  $tracked = @(& git -C $repoRoot ls-files -- backup 2>$null)
  $ErrorActionPreference = 'Stop'
  Check 'G1 git lists tracked files under backup/ (this is a checkout)' ($tracked.Count -gt 0) "repo=$repoRoot"
  $deep = @($tracked | Where-Object { $_.Substring('backup/'.Length).Contains('/') })
  Check 'G2 none of them is below a subdirectory' ($deep.Count -eq 0) ($deep -join ', ')

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
function global:Test-CfShare([string]$p) { return ($p -and $p.ToLowerInvariant().StartsWith($global:CfPrefix.ToLowerInvariant())) }
function global:net.exe { Add-Content -LiteralPath $env:CFNAS_TRACE -Value ('NET|' + ($args -join ' ')); $global:LASTEXITCODE = [int]$env:CFNAS_RC_NET; 'The command completed successfully.' }
function global:msg.exe { Add-Content -LiteralPath $env:CFNAS_TRACE -Value 'MSGEXE' }
function global:Resolve-DnsName { param($Name, $Type, $ErrorAction) [pscustomobject]@{ IPAddress = '192.0.2.77' } }
function global:robocopy.exe {
  $k = if ($args -contains '/MIR') { 'MIR' } else { 'OTHER' }
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
function global:New-Item {
  [CmdletBinding()] param([string]$ItemType, [Parameter(Position = 0)][string[]]$Path, [switch]$Force)
  if ($PSBoundParameters.ContainsKey('Path')) { $PSBoundParameters['Path'] = @($Path | ForEach-Object { Map-CfPath $_ }) }
  Microsoft.PowerShell.Management\New-Item @PSBoundParameters
}
function global:Copy-Item {
  [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Path, [string[]]$LiteralPath, [string]$Destination, [switch]$Force, [switch]$Recurse)
  if (Test-CfShare $Destination) {
    Add-Content -LiteralPath $env:CFNAS_TRACE -Value "COPY|$Destination"
    if ($env:CFNAS_FAIL_COPY -eq '1') { throw 'stub: the share refused the write' }
  }
  if ($PSBoundParameters.ContainsKey('Path')) { $PSBoundParameters['Path'] = @($Path | ForEach-Object { Map-CfPath $_ }) }
  if ($PSBoundParameters.ContainsKey('LiteralPath')) { $PSBoundParameters['LiteralPath'] = @($LiteralPath | ForEach-Object { Map-CfPath $_ }) }
  if ($PSBoundParameters.ContainsKey('Destination')) { $PSBoundParameters['Destination'] = Map-CfPath $Destination }
  Microsoft.PowerShell.Management\Copy-Item @PSBoundParameters
}
function global:Move-Item {
  [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Path, [string[]]$LiteralPath, [string]$Destination, [switch]$Force)
  if (Test-CfShare $Destination) { Add-Content -LiteralPath $env:CFNAS_TRACE -Value "MOVE|$Destination" }
  if ($PSBoundParameters.ContainsKey('Path')) { $PSBoundParameters['Path'] = @($Path | ForEach-Object { Map-CfPath $_ }) }
  if ($PSBoundParameters.ContainsKey('LiteralPath')) { $PSBoundParameters['LiteralPath'] = @($LiteralPath | ForEach-Object { Map-CfPath $_ }) }
  if ($PSBoundParameters.ContainsKey('Destination')) { $PSBoundParameters['Destination'] = Map-CfPath $Destination }
  Microsoft.PowerShell.Management\Move-Item @PSBoundParameters
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
    $vars = @{ CFNAS_TRACE = $trace; CFNAS_SHARE = $share; CFNAS_PARAMS = $Params; CFNAS_RC_NET = '0'; CFNAS_RC_MIR = ''; CFNAS_RC_OTHER = ''
      CFNAS_BAD_PARTIAL = ''; CFNAS_RACE = ''; CFNAS_SHARE_REACHABLE = ''; CFNAS_FAIL_COPY = ''; DOCKER_HOST = 'tcp://127.0.0.1:1' }
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
  $eArch = @{}; foreach ($k in (TreeHashes "$eproj\backup").Keys) { if ($k.Substring(1).Contains('\')) { $eArch[$k] = (TreeHashes "$eproj\backup")[$k] } }
  function NoLeak($r) { (-not ($r.Trace | Where-Object { $_ -like 'UNMAPPED-UNC*' })) -and ($r.Out -notmatch [regex]::Escape($fakePw)) -and ($r.Log -notmatch [regex]::Escape($fakePw)) }
  function Kinds($r) { @($r.Trace | ForEach-Object { ($_ -split '\|')[0] } | Where-Object { $_ -notin @('COPY', 'MOVE') }) -join ',' }

  Write-Host "== J: backup-to-nas.ps1 end to end (stubbed SMB, local stand-in share)"
  $r = RunStubbed $job "NasUncRoot=$unc"
  Check 'J1 happy run: exit 0 and the completion marker' (($r.Code -eq 0) -and ($r.Log -match '=== NAS sync complete ===')) "code=$($r.Code)"
  Check 'J1b call order: net use /delete, net use, slot mirror, net use /delete - no other robocopy, no alert' ((Kinds $r) -eq 'NET,NET,ROBO-MIR,NET') (Kinds $r)
  Check 'J1c the archive pass wrote to \\<ip>\backups\ai-stack\archive (temp name, then rename)' ((@($r.Trace | Where-Object { $_ -like 'COPY|\\192.0.2.77\backups\ai-stack\archive\*.cf-partial' }).Count -eq 9) -and (@($r.Trace | Where-Object { $_ -like 'MOVE|\\192.0.2.77\backups\ai-stack\archive\*' -and $_ -notlike '*.cf-partial' }).Count -eq 9)) "copies=$(@($r.Trace | Where-Object { $_ -like 'COPY|*' }).Count)"
  Check 'J1d the stand-in archive = ./backup subdirectory files, the slot = ./backups' ((SameTree $eArch (TreeHashes $nasArc)) -and (SameTree (TreeHashes "$eproj\backups") (TreeHashes (Join-Path $share "ai-stack\portal\$slot"))))
  Check 'J1e no UNC path escaped the stubs; the password appears in neither output nor log' (NoLeak $r)
  Check 'J1f the slot integrity check ran against the stand-in' ($r.Log -match 'integrity check OK')
  Check 'J1g the log says the top-level sidecar sources were skipped' (($r.Log -match '2 top-level file\(s\) skipped') -and -not (Test-Path -LiteralPath "$nasArc\generic-tar-backup.sh"))

  # F4 through the job: an interrupted copy (1980 stamp, full length, wrong content)
  $ij = "$nasArc\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar"
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ij) | Out-Null   # (a base job never made it)
  [System.IO.File]::WriteAllBytes($ij, (New-Object byte[] 500000)); Stamp $ij ([datetime]'1980-01-02')
  $r = RunStubbed $job "NasUncRoot=$unc"
  Check 'J2 an interrupted NAS copy is REPAIRED by the next run: exit 0, marker, content = local' (($r.Code -eq 0) -and ($r.Log -match 'archive: REPAIRED orphan-volumes-2026-09-13\\ai-stack_llm-gateway-db-data\.tar') -and ($r.Log -match '=== NAS sync complete ===') -and ((Sha256Of $ij) -eq $eArch['\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar'])) "code=$($r.Code)"

  # a complete NAS copy that differs -> ERROR, alert, exit 2, no marker, nothing overwritten
  $arcState = TreeHashes $nasArc
  $jt = "$eproj\backup\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar"
  $svj = Save-Local $jt
  [System.IO.File]::WriteAllBytes($jt, [byte[]]@())
  $r = RunStubbed $job "NasUncRoot=$unc"
  Check 'J3 a complete NAS copy that differs from local: exit 2, NO completion marker, NAS untouched' (($r.Code -eq 2) -and ($r.Log -notmatch '=== NAS sync complete ===') -and (SameTree $arcState (TreeHashes $nasArc))) "code=$($r.Code)"
  Check 'J3b ... an [ERROR] MISMATCH line with the Trust verdict, one alert, the integrity check still ran, session torn down' (($r.Log -match '\[ERROR\] archive: MISMATCH orphan-volumes-2026-09-13\\ai-stack_llm-gateway-db-data\.tar .*NOTHING was overwritten\. Trust: UNKNOWN') -and (@($r.Trace | Where-Object { $_ -like 'ALERT|nas-backup.failure|*archive pass: 1 file*' }).Count -eq 1) -and ($r.Log -match 'verifying a sample') -and ($r.Trace[-1] -like 'NET|use*/delete*'))
  $r = RunStubbed $job "NasUncRoot=$unc|DryRun"
  Check 'J4 dry run with that mismatch: exit 2, no DRY RUN complete, alert, nothing written' (($r.Code -eq 2) -and ($r.Log -notmatch 'DRY RUN\) complete') -and (@($r.Trace | Where-Object { $_ -like 'ALERT|*' }).Count -eq 1) -and -not ($r.Trace | Where-Object { $_ -like 'COPY|*' -or $_ -like 'MOVE|*' })) "code=$($r.Code)"
  Restore-Local $svj

  Blob "$eproj\backup\models\later.json" 700
  $shareBefore = TreeHashes $share
  $r = RunStubbed $job "NasUncRoot=$unc|DryRun"
  Check 'J5 dry run ok: exit 0, "(DRY RUN) complete", WOULD-COPY logged, mirror carries /L, the share unchanged' (($r.Code -eq 0) -and ($r.Log -match '=== NAS sync \(DRY RUN\) complete ===') -and ($r.Log -match 'archive: WOULD-COPY models\\later\.json') -and (@($r.Trace | Where-Object { $_ -like 'ROBO-MIR*' -and $_ -match ' /L ' }).Count -eq 1) -and (SameTree $shareBefore (TreeHashes $share))) "code=$($r.Code)"

  $r = RunStubbed $job "NasUncRoot=$unc" @{ CFNAS_FAIL_COPY = '1' }
  Check 'J6 the share refuses the write: exit 2, no marker, FAIL-COPY [ERROR], alert, final name never created' (($r.Code -eq 2) -and ($r.Log -notmatch '=== NAS sync complete ===') -and ($r.Log -match '\[ERROR\] archive: FAIL-COPY models\\later\.json') -and (@($r.Trace | Where-Object { $_ -like 'ALERT|*' }).Count -eq 1) -and -not (Test-Path -LiteralPath "$nasArc\models\later.json")) "code=$($r.Code)"

  $r = RunStubbed $job "NasUncRoot=$unc" @{ CFNAS_RC_MIR = '8' }
  Check 'J6b slot mirror rc 8: exit 2, no archive pass, alert' (($r.Code -eq 2) -and ($r.Log -notmatch 'archive pass:') -and (@($r.Trace | Where-Object { $_ -like 'ALERT|*' }).Count -ge 1)) "code=$($r.Code)"

  foreach ($sp in $SlotSpellings) {
    $r = RunStubbed $job ("NasUncRoot=$unc|NasArchiveRoot=\\cfnas-fake.invalid\backups\ai-stack" + $sp)
    Check "J7 archive root [...ai-stack$sp]: exit 1, alert, no net use, no robocopy, no write" (($r.Code -eq 1) -and (@($r.Trace | Where-Object { $_ -like 'ALERT|*archive root rejected*' }).Count -eq 1) -and -not ($r.Trace | Where-Object { $_ -like 'NET|*' -or $_ -like 'ROBO-*' -or $_ -like 'COPY|*' })) "code=$($r.Code) trace=$($r.Trace -join ' ; ')"
  }

  Rename-Item -LiteralPath "$eproj\backup" -NewName 'backup-hidden'
  $r = RunStubbed $job "NasUncRoot=$unc"
  Rename-Item -LiteralPath "$eproj\backup-hidden" -NewName 'backup'
  Check 'J8 no ./backup: WARN, archive pass skipped, run still complete' (($r.Code -eq 0) -and ($r.Log -match '\[WARN\] archive source .* does not exist') -and ($r.Log -notmatch 'archive pass:') -and ($r.Log -match '=== NAS sync complete ==='))
  $r = RunStubbed $job "NasUncRoot=$unc|NoArchive"
  Check 'J9 -NoArchive: no archive pass, run complete' (($r.Code -eq 0) -and ($r.Log -notmatch 'archive pass:') -and -not ($r.Trace | Where-Object { $_ -like 'COPY|*' }) -and ($r.Log -match '=== NAS sync complete ==='))
  $r = RunStubbed $job "NasUncRoot=$unc" @{ CFNAS_RC_NET = '2' }
  Check 'J10 net use fails: exit 1, no robocopy, no write, password not leaked' (($r.Code -eq 1) -and -not ($r.Trace | Where-Object { $_ -like 'ROBO-*' -or $_ -like 'COPY|*' }) -and (NoLeak $r))

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
  Check 'Z1 ./backup stand-in unchanged by every run above' (SameTree $srcAll (TreeHashes $backup))
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
