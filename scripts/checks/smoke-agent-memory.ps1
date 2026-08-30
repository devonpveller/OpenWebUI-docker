# smoke-agent-memory.ps1 - memory-plane Phase 1.3. Start the REAL server and call it.
#
# WHAT WAS UNPROVEN BEFORE THIS (documentation/notes/agent-memory-writeback-findings.md F2):
# the agent-memory tool's LOGIC is well covered - 42 unit tests over policy and SQL shape,
# plus the offline harness executing the statements against the real schema. What nothing
# covered was the DOORS. `POST /agent-memory/writeback` had never been called. Its auth
# predicate, its JSON parsing, its 422-on-refusal mapping and - most of all - whether the
# route is REACHABLE were guaranteed by a comment saying it is registered before
# `app.all("*")`. If that ordering ever breaks, the MCP catch-all swallows the route and
# every REST call answers as a transport instead; no unit test can see it, because the
# ordering only exists once the server is assembled.
#
# So this starts the actual container and speaks HTTP to it. Everything it asserts is
# something only a running server can answer.
#
# ISOLATION, deliberately: a throwaway network, a throwaway database on the real initdb
# chain, and a STUB embedding endpoint. It touches no live plane and needs no GPU. The
# image is tagged :smoke - never :local, which is the tag production runs from
# (CLAUDE.md: "Test images tag :wt-<id>; prod containers and :local tags are a gated
# deploy, not a test" - this repo has already had one accident there).
#
#   .\scripts\checks\smoke-agent-memory.ps1
#   .\scripts\checks\smoke-agent-memory.ps1 -KeepUp     # leave it up to poke at
#
# Exit: 0 = all checks passed | 1 = one or more failed

[CmdletBinding()]
param(
    [switch]$KeepUp,
    [int]$HostPort = 18099
)

$ErrorActionPreference = "Continue"   # native docker stderr must never be fatal (PS 5.1)
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root
. (Join-Path $PSScriptRoot "lib\ob-initdb.ps1")

$fails = 0
function Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Pass($t) { Write-Host "  PASS  $t" -ForegroundColor Green }
function Fail($t) { Write-Host "  FAIL  $t" -ForegroundColor Red; $script:fails++ }

$NET   = "am-smoke-net"
$DB    = "am-smoke-db"
$STUB  = "am-smoke-embed"
$SRV   = "am-smoke-mcp"
$KEY   = "smoke-key-not-a-secret"
$IMAGE = "openbrain-mcp-server:smoke"

function Remove-SmokeStack {
    docker rm -f $SRV $STUB $DB 2>$null | Out-Null
    docker network rm $NET 2>$null | Out-Null
}
Remove-SmokeStack

try {
    # --- 1. the database, on the chain compose actually mounts ---------------------------
    Section "throwaway database (real initdb chain)"
    docker network create $NET 2>$null | Out-Null
    $chain = Get-ObInitChain -ComposePath (Join-Path $root "OB1\docker\docker-compose.yml")
    if ($chain.Count -lt 1) { Fail "could not parse the initdb chain from compose"; throw "no chain" }
    $tmp = Join-Path $env:TEMP "am-smoke-initdb"
    $staged = Copy-ObInitChain -Chain $chain -SourceDir (Join-Path $root "OB1\docker") -TargetDir $tmp
    if ($staged -ne $chain.Count) { Fail "staged $staged of $($chain.Count) migrations - a mount names a missing file" }
    else { Pass "staged the full chain ($staged migrations)" }
    if (Start-ObInitdb -Name $DB -InitDir $tmp -DockerArgs @("--network", $NET)) {
        Pass "initdb finished (entrypoint reported init process complete)"
    } else { Fail "initdb did not complete - nothing below is trustworthy"; throw "db not ready" }
    $initErrs = Get-ObInitdbErrors -Name $DB
    if ($initErrs) { Write-Host ($initErrs -join "`n") -ForegroundColor Red; Fail "init chain had errors" }
    else { Pass "init chain ran without errors" }

    # --- 2. a stub embedding endpoint ---------------------------------------------------
    # The writeback embeds its content before writing. Pointing at the real embedding lane
    # would make this smoke test depend on the GPU plane being up, which is the opposite of
    # what a smoke test should need. The stub returns a correctly-SHAPED 1024-dim vector
    # (bge-m3's width, per init.sql) - the shape is what the insert cares about; the values
    # are irrelevant to whether the door works.
    Section "stub embedding endpoint"
    $stubLines = @(
        'Deno.serve({ port: 8080 }, (req) => {',
        '  if (!req.url.includes("/embeddings")) return new Response("no", { status: 404 });',
        '  return Response.json({ data: [{ embedding: Array(1024).fill(0.001) }] });',
        '});'
    )
    $stubPath = Join-Path $env:TEMP "am-smoke-embed.ts"
    Set-Content -Path $stubPath -Value $stubLines -Encoding ASCII
    $stubFwd = ($stubPath -replace '\\', '/')
    docker run -d --name $STUB --network $NET -v "${stubFwd}:/stub.ts:ro" `
        denoland/deno:2.3.3 run --allow-net /stub.ts | Out-Null
    $stubUp = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep 1
        if (docker logs $STUB 2>&1 | Select-String -Quiet "Listening") { $stubUp = $true; break }
    }
    if ($stubUp) { Pass "stub embedding endpoint listening" }
    else { docker logs $STUB 2>&1 | Select-Object -Last 10 | Write-Host; Fail "stub embedding endpoint never came up"; throw "no stub" }

    # --- 3. the real server image -------------------------------------------------------
    Section "build and start the real server"
    docker build -t $IMAGE (Join-Path $root "OB1\integrations\kubernetes-deployment") 2>&1 |
        Select-Object -Last 1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "docker build failed for $IMAGE"; throw "build failed" }
    Pass "image built as $IMAGE (never :local - that is the production tag)"

    docker run -d --name $SRV --network $NET -p "${HostPort}:8000" `
        -e DB_HOST=$DB -e DB_PORT=5432 -e DB_NAME=openbrain -e DB_USER=postgres `
        -e DB_PASSWORD=test -e MCP_ACCESS_KEY=$KEY -e PORT=8000 `
        -e "EMBEDDING_API_BASE=http://${STUB}:8080" -e EMBEDDING_API_KEY=stub `
        -e EMBEDDING_MODEL=stub-embed $IMAGE | Out-Null

    # WAIT FOR THE SERVER TO ANSWER, and take a 401 as an answer - it means the process is
    # up and routing. Polling a fixed sleep here would be the same race the initdb marker
    # note describes, one layer up.
    $srvUp = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep 1
        try {
            $null = Invoke-WebRequest -Uri "http://127.0.0.1:$HostPort/agent-memory/writeback" `
                -Method POST -Headers @{ "x-brain-key" = "wrong" } -Body "{}" `
                -ContentType "application/json" -UseBasicParsing -TimeoutSec 3
            $srvUp = $true; break
        } catch {
            if ($_.Exception.Response) { $srvUp = $true; break }
        }
    }
    if ($srvUp) { Pass "server is answering on :$HostPort" }
    else { docker logs $SRV 2>&1 | Select-Object -Last 25 | Write-Host; Fail "server never answered"; throw "no server" }

    # --- 4. the doors -------------------------------------------------------------------
    function Invoke-Door {
        param([string]$Path, [hashtable]$Body, [string]$Key = $KEY)
        $headers = @{}
        if ($Key) { $headers["x-brain-key"] = $Key }
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$HostPort$Path" -Method POST `
                 -Headers $headers -Body ($Body | ConvertTo-Json -Depth 6 -Compress) `
                 -ContentType "application/json" -UseBasicParsing -TimeoutSec 60
            return @{ Status = [int]$r.StatusCode; Body = ($r.Content | ConvertFrom-Json) }
        } catch {
            $resp = $_.Exception.Response
            if (-not $resp) { return @{ Status = -1; Body = $_.Exception.Message } }
            # THE BODY OF AN HTTP ERROR IS IN $_.ErrorDetails.Message, NOT IN THE STREAM.
            # Invoke-WebRequest (PS 5.1) has already read and buffered the error response,
            # so calling GetResponseStream().ReadToEnd() here returns an EMPTY string - the
            # position is at the end. Verified against this very server: a 422 whose body is
            # {"ok":false,...} read back as len=0.
            #
            # That matters more than it looks. Every assertion on a refusal body would have
            # compared against $null and FAILED while the server was behaving correctly -
            # a check that cries wolf, which is the failure mode that gets checks deleted.
            # It only surfaced as a loud error because StrictMode happened to be in effect.
            $txt = ""
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $txt = $_.ErrorDetails.Message }
            else {
                try {
                    $stream = $resp.GetResponseStream()
                    if ($stream.CanSeek) { $stream.Position = 0 }
                    $txt = (New-Object System.IO.StreamReader($stream)).ReadToEnd()
                } catch { $txt = "" }
            }
            $parsed = $txt
            try { $parsed = $txt | ConvertFrom-Json } catch { }
            return @{ Status = [int]$resp.StatusCode; Body = $parsed }
        }
    }

    Section "the REST door (never called before this script existed)"
    # A bad key CANNOT prove the route is reachable: the MCP catch-all also answers 401, so
    # both orderings look identical here. It is still worth asserting - it is the auth
    # predicate - but the reachability proof is the GOOD-key call below, where the catch-all
    # would try to run an MCP transport over this body and could not return the writeback
    # contract.
    $unauth = Invoke-Door -Path "/agent-memory/writeback" -Body @{ workspace_id = "ws-smoke" } -Key "wrong-key"
    if ($unauth.Status -eq 401) { Pass "REST writeback rejects a bad key (401)" }
    else { Fail "REST writeback with a bad key returned $($unauth.Status), expected 401" }

    $write = Invoke-Door -Path "/agent-memory/writeback" -Body @{
        workspace_id = "ws-smoke"; project_id = "proj-smoke"
        summary = "smoke summary"; content = "a lesson worth keeping, written by the smoke test"
        memory_type = "lesson"; idempotency_key = "smoke-1"
    }
    if ($write.Status -eq 200 -and $write.Body.ok -eq $true -and $write.Body.memory_id) {
        Pass "REST writeback returned the writeback contract, so the route is REACHABLE"
    } else {
        Write-Host ($write | ConvertTo-Json -Depth 6) -ForegroundColor Red
        Fail "REST writeback did not return ok - the route may be swallowed by the MCP catch-all"
    }

    # A 200 that wrote nothing is the failure mode this whole plane exists to prevent, so
    # the assertion is on the DATABASE, not on the response.
    # PLAN §1 locks these. review_status is 'pending', NOT 'evidence_only' - a memory no
    # human has looked at is not recallable by default, which is what the review door is
    # for. An earlier version of this script asserted 'evidence_only', because the write
    # default had been changed to satisfy a mis-stated invariant.
    $row = docker exec $DB psql -U postgres -d openbrain -tA -c `
        "SELECT review_status || '|' || visibility || '|' || coalesce(project_id,'-') || '|' || coalesce(metadata->>'exposure','-') FROM agent_memories WHERE workspace_id = 'ws-smoke'"
    $rowTxt = ($row | Out-String).Trim()
    if ($rowTxt -match "pending\|project\|proj-smoke\|ops") { Pass "the memory is IN the database with the LOCKED policy defaults ($rowTxt)" }
    else { Fail "expected pending|project|proj-smoke|ops in agent_memories, got '$rowTxt'" }

    $audit = (docker exec $DB psql -U postgres -d openbrain -tA -c `
        "SELECT count(*) FROM agent_memory_audit_events WHERE workspace_id = 'ws-smoke' AND event_type = 'memory_written'" | Out-String).Trim()
    if ($audit -eq "1") { Pass "the audit event was written in the same transaction" }
    else { Fail "expected exactly 1 audit event, got '$audit'" }

    Section "idempotency and refusal, through the door"
    $again = Invoke-Door -Path "/agent-memory/writeback" -Body @{
        workspace_id = "ws-smoke"; summary = "smoke summary"
        content = "a lesson worth keeping, written by the smoke test"
        memory_type = "lesson"; idempotency_key = "smoke-1"
    }
    if ($again.Status -eq 200 -and $again.Body.duplicate -eq $true -and $again.Body.memory_id -eq $write.Body.memory_id) {
        Pass "a repeated idempotency_key returns the SAME memory, marked duplicate"
    } else { Fail "idempotent retry did not return the original memory as a duplicate" }

    # Same key, DIFFERENT workspace. A real defect, found in review: the lookup matched on
    # idempotency_key alone, so a second tenant was handed the first tenant's memory id and
    # told duplicate:true while its own memory was never written. Two tenants sharing an
    # obvious key string is the ordinary case, not an attack.
    $other = Invoke-Door -Path "/agent-memory/writeback" -Body @{
        workspace_id = "ws-other"; summary = "different tenant"
        content = "an unrelated lesson from another workspace"
        memory_type = "lesson"; idempotency_key = "smoke-1"
    }
    if ($other.Status -eq 200 -and $other.Body.duplicate -ne $true -and $other.Body.memory_id -ne $write.Body.memory_id) {
        Pass "the same key in ANOTHER workspace writes its own memory (no cross-tenant bleed)"
    } else {
        Write-Host ($other | ConvertTo-Json -Depth 6) -ForegroundColor Red
        Fail "cross-tenant idempotency: the second workspace did not get its own memory"
    }

    # 422, not 400 and not 500: the request was well-formed, the CONTENT was refused. The
    # fixture is assembled at runtime so no credential-shaped literal is committed - GitHub
    # push protection rejects those, correctly, and it cannot tell a fixture from the real
    # thing.
    $secret = ("AKIA" + "IOSFODNN7EXAMPLE")
    $refused = Invoke-Door -Path "/agent-memory/writeback" -Body @{
        workspace_id = "ws-smoke"; summary = "should be refused"
        content = "here is the key $secret you asked for"; memory_type = "lesson"
    }
    if ($refused.Status -eq 422 -and $refused.Body.ok -eq $false -and $refused.Body.refused -eq "secret_shaped") {
        Pass "secret-shaped content is refused with 422 and names the reason"
    } else {
        Write-Host ($refused | ConvertTo-Json -Depth 6) -ForegroundColor Red
        Fail "expected 422 + ok:false + refused:secret_shaped, got $($refused.Status)"
    }
    # And it must NOT have been written. A refusal that returns 422 and stores the row
    # anyway is worse than no screen at all.
    $leaked = (docker exec $DB psql -U postgres -d openbrain -tA -c `
        "SELECT count(*) FROM agent_memories WHERE workspace_id = 'ws-smoke' AND summary = 'should be refused'" | Out-String).Trim()
    if ($leaked -eq "0") { Pass "the refused memory was NOT written to the database" }
    else { Fail "a refused writeback still stored $leaked row(s)" }

    $badStatus = 0
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:$HostPort/agent-memory/writeback" -Method POST `
            -Headers @{ "x-brain-key" = $KEY } -Body "{not json" -ContentType "application/json" `
            -UseBasicParsing -TimeoutSec 10
    } catch { if ($_.Exception.Response) { $badStatus = [int]$_.Exception.Response.StatusCode } }
    if ($badStatus -eq 400) { Pass "malformed JSON is a 400, not a 500" }
    else { Fail "malformed JSON returned $badStatus, expected 400" }

    # --- 5. THE PLANE-AGREEMENT INVARIANT, as PLAN §1.3 states it -----------------------
    # "the default writeback VISIBILITY/EXPOSURE must be provably returned by the default
    # recall scope". Through two doors and a database, not two functions agreeing in a stub.
    #
    # The review gate is asserted in the OPPOSITE direction from the earlier version of this
    # script: a fresh write is NOT returned by a conservative recall, and IS returned once
    # the caller opts in. That is §1.3's "conservative recall returns nothing pending".
    Section "the plane-agreement invariant, through the doors"
    $recall = Invoke-Door -Path "/agent-memory/recall" -Body @{
        workspace_id = "ws-smoke"; project_id = "proj-smoke"
        query = "a lesson worth keeping"; limit = 8
    }
    $ids = @()
    if ($recall.Body -and $recall.Body.items) { $ids = @($recall.Body.items | ForEach-Object { $_.memory_id }) }
    if ($recall.Status -eq 200 -and $ids -notcontains $write.Body.memory_id) {
        Pass "a conservative recall returns NOTHING pending - the review gate is real"
    } else { Fail "an unreviewed memory was returned by the conservative recall" }

    $recallU = Invoke-Door -Path "/agent-memory/recall" -Body @{
        workspace_id = "ws-smoke"; project_id = "proj-smoke"
        query = "a lesson worth keeping"; limit = 8; include_unconfirmed = $true
    }
    $idsU = @()
    if ($recallU.Body -and $recallU.Body.items) { $idsU = @($recallU.Body.items | ForEach-Object { $_.memory_id }) }
    if ($recallU.Status -eq 200 -and $idsU -contains $write.Body.memory_id) {
        Pass "include_unconfirmed DOES return it - reachable, just not by default"
    } else {
        Write-Host ($recallU | ConvertTo-Json -Depth 6) -ForegroundColor Red
        Fail "include_unconfirmed did not return the pending memory"
    }
    if ($idsU -notcontains $other.Body.memory_id) { Pass "another workspace's memory is not in the recall" }
    else { Fail "CROSS-WORKSPACE LEAK: ws-other's memory was returned to a ws-smoke recall" }

    # --- 6. §1.1 ACCESS BOUNDS WRITES ---------------------------------------------------
    # The binding invariant (operator, 2026-08-25): a record's maximum exposure equals the
    # access plane of the context that wrote it, enforced mechanically. Two halves, and
    # only a running server can answer either.
    Section "the exposure boundary (PLAN 1.1)"

    # (a) THE MECHANICAL RULE BEATS THE CALLER'S CLAIM.
    #     This door forces 'ops' (the internal lane stamps per the taint rule), so a caller
    #     ASKING for ops is not an escalation and proves nothing. What must hold is that a
    #     request the rule demotes lands personal no matter what the body claims - the
    #     caller says tainted, and also says exposure:'ops' in two places, and loses.
    $liar = Invoke-Door -Path "/agent-memory/writeback" -Body @{
        workspace_id = "ws-smoke"; project_id = "proj-smoke"
        summary = "claims to be ops"; content = "a lesson from a tainted effort"
        memory_type = "lesson"; tainted = $true
        exposure = "ops"; metadata = @{ exposure = "ops" }
    }
    if ($liar.Status -eq 200) {
        $liarExp = (docker exec $DB psql -U postgres -d openbrain -tA -c `
            "SELECT metadata->>'exposure' FROM agent_memories WHERE id = '$($liar.Body.memory_id)'" | Out-String).Trim()
        if ($liarExp -eq "personal") { Pass "a tainted write claiming 'ops' is stamped personal anyway" }
        else { Fail "EXPOSURE ESCALATION: a tainted caller reached plane '$liarExp'" }
    } else { Fail "the exposure-escalation probe did not write ($($liar.Status))" }

    # (b) PII DEMOTES, IT NEVER REJECTS. Code and ops prose are full of email-shaped
    #     strings, so a gate that refused them would be switched off. The memory is kept -
    #     on the narrower plane.
    $pii = Invoke-Door -Path "/agent-memory/writeback" -Body @{
        workspace_id = "ws-smoke"; project_id = "proj-smoke"
        summary = "contains an address"; content = "ping someone@example.com about the drain"
        memory_type = "lesson"
    }
    if ($pii.Status -eq 200 -and $pii.Body.ok -eq $true) {
        $piiExp = (docker exec $DB psql -U postgres -d openbrain -tA -c `
            "SELECT metadata->>'exposure' FROM agent_memories WHERE id = '$($pii.Body.memory_id)'" | Out-String).Trim()
        if ($piiExp -eq "personal") { Pass "PII content was STORED and demoted to personal, not rejected" }
        else { Fail "expected PII content to be demoted to personal, got '$piiExp'" }
    } else { Fail "PII content was REFUSED - the gate must demote, never reject ($($pii.Status))" }

    # (c) THE MIRROR. The exposure label is copied onto the linked thought, so the generic
    #     search_thoughts lane enforces the same boundary. Without it a memory could be
    #     unreachable through the agent-memory gate and readable through another lane,
    #     which would make the gate decorative.
    $mirror = (docker exec $DB psql -U postgres -d openbrain -tA -c `
        "SELECT t.metadata->>'exposure' FROM thoughts t JOIN agent_memories am ON am.thought_id = t.id WHERE am.id = '$($pii.Body.memory_id)'" | Out-String).Trim()
    if ($mirror -eq "personal") { Pass "the exposure label is mirrored onto the linked thought ($mirror)" }
    else { Fail "the thought's exposure label is '$mirror', not mirrored from the memory" }

    # (d) AND THE PERSONAL PLANE IS NOT IN A DEFAULT RECALL. This is the read half of the
    #     invariant; (a) and (b) only proved the write half.
    $recallP = Invoke-Door -Path "/agent-memory/recall" -Body @{
        workspace_id = "ws-smoke"; project_id = "proj-smoke"
        query = "drain"; limit = 25; include_unconfirmed = $true
    }
    $idsP = @()
    if ($recallP.Body -and $recallP.Body.items) { $idsP = @($recallP.Body.items | ForEach-Object { $_.memory_id }) }
    if ($idsP -notcontains $pii.Body.memory_id -and $idsP -notcontains $liar.Body.memory_id) {
        Pass "personal-plane memories are NOT returned by a default recall"
    } else { Fail "EXPOSURE LEAK: a personal-plane memory was returned by a default recall" }

} catch {
    Write-Host ("  aborted: " + $_.Exception.Message) -ForegroundColor Red
    $fails++
} finally {
    if ($KeepUp) {
        Write-Host "`n-KeepUp: leaving $SRV / $DB / $STUB on network $NET" -ForegroundColor Yellow
        Write-Host "  tear down with: docker rm -f $SRV $STUB $DB; docker network rm $NET"
    } else {
        Remove-SmokeStack
    }
}

Write-Host ""
if ($fails -eq 0) { Write-Host "ALL AGENT-MEMORY SMOKE CHECKS PASSED" -ForegroundColor Green; exit 0 }
Write-Host "$fails SMOKE CHECK(S) FAILED" -ForegroundColor Red
exit 1
