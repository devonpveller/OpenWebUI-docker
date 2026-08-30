<#
.SYNOPSIS
  Offline test harness for the Quartz-4-expansion work (workbench + extract +
  schema + compiler). Nothing here touches the live/prod stack or its data.

.DESCRIPTION
  Phase "unit" (default): static checks + unit tests + a throwaway-pgvector
    schema migration run. Fast, no images built, no prod contact.
  Phase "e2e": builds the new images and brings up an ISOLATED throwaway stack
    under project `ob-test` with fresh volumes, smoke-tests /health, tears down.
  Phase "all": both.

.NOTES
  Run from the repo root:  .\scripts\test-quartz4-offline.ps1 -Phase unit
  e2e needs the shared external nets (llm-net/app-net) to exist (main stack up)
  and the loopback debug ports 8814/8815 free — stop prod's
  openbrain-workbench/-extract first, or expect a port-bind error.
#>
param([ValidateSet("unit", "e2e", "all")] [string]$Phase = "unit")

$ErrorActionPreference = "Continue"
$root = (Get-Location).Path
$rootFwd = $root -replace '\\', '/'
$ob1 = "OB1/docker/docker-compose.yml"
$fails = 0
function Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Pass($t) { Write-Host "  PASS  $t" -ForegroundColor Green }
function Fail($t) { Write-Host "  FAIL  $t" -ForegroundColor Red; $script:fails++ }

function Invoke-Unit {
  Section "Static checks"
  docker compose -f $ob1 config -q; if ($LASTEXITCODE -eq 0) { Pass "docker compose config" } else { Fail "compose config" }
  foreach ($f in @(
      "OB1/recipes/_shared/slug.mjs",
      "OB1/recipes/_shared/citations.mjs",
      "OB1/recipes/_shared/write-if-changed.mjs",
      "OB1/recipes/_shared/source-leaf.mjs",
      "OB1/recipes/entity-wiki/generate-wiki.mjs",
      "OB1/docker/wiki-service/wiki-service.mjs",
      "OB1/docker/wiki-viewer/serve.mjs",
      "OB1/docker/wiki-viewer/derive-graph-index.mjs",
      "OB1/recipes/wiki-synthesis/scripts/synthesize-notebooks.mjs")) {
    node --check $f; if ($LASTEXITCODE -eq 0) { Pass "node --check $f" } else { Fail "node --check $f" }
  }
  python -m py_compile OB1/docker/extract/app.py; if ($LASTEXITCODE -eq 0) { Pass "py_compile extract/app.py" } else { Fail "py_compile" }

  Section "Node unit tests (slug + citations + write-if-changed + notebook synth)"
  node --test OB1/recipes/_shared/slug.test.mjs OB1/recipes/_shared/citations.test.mjs `
    OB1/recipes/_shared/write-if-changed.test.mjs OB1/recipes/entity-wiki/generate-wiki.test.mjs `
    OB1/recipes/wiki-synthesis/scripts/synthesize-notebooks.test.mjs
  if ($LASTEXITCODE -eq 0) { Pass "node --test" } else { Fail "node --test" }

  Section "Deno: type-check + unit tests (workbench)"
  docker run --rm -v "${rootFwd}/OB1/docker/workbench:/app" -v "${rootFwd}/OB1/recipes:/recipes:ro" `
    -w /app denoland/deno:2.3.3 sh -c "deno check src/main.ts && deno test src/util/paths_test.ts src/util/chunk_test.ts"
  if ($LASTEXITCODE -eq 0) { Pass "deno check + deno test" } else { Fail "deno check/test" }

  Section "Deno: agent-memory policy (memory-plane P1.2)"
  # Pure logic - no database, no network - so the plane's write/read policy is checkable
  # without a stack. The invariant it pins is that a DEFAULT writeback is returned by the
  # DEFAULT recall; upstream's Hermes integration ships the opposite and its plane is
  # silently always empty.
  docker run --rm -v "${rootFwd}/OB1/integrations/kubernetes-deployment:/app" `
    -w /app denoland/deno:2.3.3 sh -c "deno check agent-memory-policy.ts agent-memory-policy.test.ts agent-memory.ts agent-memory.test.ts index.ts && deno test agent-memory-policy.test.ts agent-memory.test.ts"
  if ($LASTEXITCODE -eq 0) { Pass "agent-memory policy: deno check + test" } else { Fail "agent-memory policy: deno check/test" }

  Section "Caddy validate (portal route)"
  docker run --rm -e PUBLIC_DOMAIN=example.com -e ACME_EMAIL=a@b.c -e WORKBENCH_KEY=k `
    -v "${rootFwd}/config/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.8.4-alpine `
    caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile
  if ($LASTEXITCODE -eq 0) { Pass "caddy validate" } else { Fail "caddy validate" }

  Section "Schema migrations on a throwaway pgvector (fresh volume)"
  $tmp = (Join-Path $env:TEMP "ob-initdb") -replace '\\', '/'
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory $tmp -Force | Out-Null
  # THE CHAIN IS DERIVED FROM COMPOSE, never hardcoded here.
  #
  # It used to be a literal list, and it had silently gone stale: it stopped at
  # 88-init-import-jobs while compose mounted twenty files. So this harness was proving
  # "fresh apply works" for a chain that was not the real one - seven migrations, including
  # every one added since, were never exercised. A test that checks a copy of reality
  # eventually checks something reality no longer resembles.
  $map = @()
  foreach ($m in ([regex]'\./(init[a-z0-9.\-]*\.sql):/docker-entrypoint-initdb\.d/([0-9a-z]+-init[a-z0-9.\-]*\.sql)').Matches((Get-Content -Raw $ob1))) {
    $map += , @($m.Groups[1].Value, $m.Groups[2].Value)
  }
  if ($map.Count -lt 1) { Fail "could not parse any initdb mounts out of $ob1" }
  else { Pass "initdb chain derived from compose ($($map.Count) migrations)" }

  # INTEGRITY, BOTH DIRECTIONS. Each half is a real failure that has happened here:
  #   - a mount naming a missing file breaks `up` on a FRESH volume only - discovered at
  #     the worst possible moment, during a rebuild;
  #   - a migration applied by hand to the live DB but never mounted means a rebuilt
  #     database silently differs from the running one. Two files were in exactly that
  #     state (agent-memory, and wiki-pages-links) before this check existed.
  $missingFiles = @($map | Where-Object { -not (Test-Path "OB1/docker/$($_[0])") } | ForEach-Object { $_[0] })
  if ($missingFiles.Count) { Fail ("compose mounts files that do not exist: " + ($missingFiles -join ", ")) }
  else { Pass "every initdb mount points at a real file" }

  $mounted = @($map | ForEach-Object { $_[0] })
  $unmounted = @(Get-ChildItem "OB1/docker" -Filter "init*.sql" | Select-Object -ExpandProperty Name |
                 Where-Object { $mounted -notcontains $_ })
  if ($unmounted.Count) {
    Fail ("init*.sql present but NOT in the initdb chain (a fresh volume would not get these): " +
          ($unmounted -join ", "))
  } else { Pass "every OB1/docker/init*.sql is mounted in the chain" }

  foreach ($m in $map) {
    if (Test-Path "OB1/docker/$($m[0])") { Copy-Item "OB1/docker/$($m[0])" (Join-Path $tmp $m[1]) }
  }
  docker rm -f ob-initdb-test 2>$null | Out-Null
  docker run -d --name ob-initdb-test -e POSTGRES_DB=openbrain -e POSTGRES_USER=postgres `
    -e POSTGRES_PASSWORD=test -v "${tmp}:/docker-entrypoint-initdb.d:ro" pgvector/pgvector:pg16 | Out-Null
  # POLL, do not sleep a magic number - and poll for the RIGHT marker.
  # The chain grew from 13 files to 20 and the old fixed 18s became a coin flip: too short
  # and the harness greps a half-finished log and calls it clean.
  #
  # "database system is ready to accept connections" appears
  # TWICE: once for the TEMPORARY server postgres runs the initdb scripts against, and
  # again when the real server starts. Polling for it catches the first one - i.e. BEFORE
  # the migrations have run - and every verify query then reports 0 for everything the
  # chain was supposed to create. (The previous fixed `Start-Sleep 18` had the same race,
  # just less visibly; it only ever passed because nothing downstream was asserted.)
  # "PostgreSQL init process complete" is the entrypoint's own end-of-initdb marker.
  $ready = $false
  for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep 2
    if ((docker logs ob-initdb-test 2>&1 | Select-String -Quiet "PostgreSQL init process complete")) {
      $ready = $true; break
    }
  }
  if ($ready) { Pass "initdb finished (entrypoint reported init process complete)" }
  else { Fail "initdb did not complete within 180s - results below are not trustworthy" }

  $errLines = docker logs ob-initdb-test 2>&1 | Select-String -Pattern "ERROR|FATAL" |
    Where-Object { $_ -notmatch "does not exist, skipping" }
  if ($errLines) { Write-Host ($errLines -join "`n") -ForegroundColor Red; Fail "init had errors" }
  else { Pass "init chain ran without errors" }
  $verify = docker exec ob-initdb-test psql -U postgres -d openbrain -tA -c @"
SELECT 'threads.slug',count(*) FROM information_schema.columns WHERE table_name='threads' AND column_name='slug'
UNION ALL SELECT 'source_revisions',count(*) FROM information_schema.tables WHERE table_name='source_revisions'
UNION ALL SELECT 'retraction_committed_at',count(*) FROM information_schema.columns WHERE table_name='sources' AND column_name='retraction_committed_at'
UNION ALL SELECT 'content_types_rows',count(*) FROM content_types
UNION ALL SELECT 'content_type_fk',count(*) FROM pg_constraint WHERE conname='sources_content_type_fkey'
UNION ALL SELECT 'old_check_gone(0)',count(*) FROM pg_constraint WHERE conname='sources_content_type_check'
UNION ALL SELECT 'match_source_chunks',count(*) FROM pg_proc WHERE proname='match_source_chunks'
UNION ALL SELECT 'import_jobs',count(*) FROM information_schema.tables WHERE table_name='import_jobs'
UNION ALL SELECT 'agent_memory_tables(8)',count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'agent_memor%'
UNION ALL SELECT 'agent_memory_trigger(1)',count(*) FROM pg_trigger WHERE tgname='trg_agent_memories_updated_at'
UNION ALL SELECT 'agent_memory_functions(2)',count(*) FROM pg_proc WHERE proname IN ('agent_memories_set_updated_at','agent_memory_hash_text')
UNION ALL SELECT 'wiki_links_gin(1)',count(*) FROM pg_indexes WHERE indexname='idx_wiki_pages_links_gin';
"@
  Write-Host $verify
  # The counts above are printed for the operator, but the ones that must HOLD are asserted -
  # a verify query nobody checks is decoration. agent-memory is memory-plane P1.1; the wiki
  # links GIN index is what stops every graph render sequential-scanning ~41k rows.
  # Normalise first: `docker exec` may hand back one multi-line string or an array of
  # lines depending on how it is invoked, and matching against the wrong one silently
  # matches nothing - which reads exactly like a failing assertion.
  $verifyLines = (($verify | Out-String) -split "`r?`n") | Where-Object { $_.Trim() }
  foreach ($expect in @(@('agent_memory_tables(8)', '8'), @('agent_memory_trigger(1)', '1'),
                        @('agent_memory_functions(2)', '2'), @('wiki_links_gin(1)', '1'))) {
    $line = @($verifyLines | Where-Object { $_.StartsWith($expect[0] + '|') }) | Select-Object -First 1
    if ($line -and (($line -split '\|')[1].Trim() -eq $expect[1])) { Pass "fresh volume: $($expect[0])" }
    else { Fail "fresh volume: $($expect[0]) - got '$line'" }
  }
  # THE MODULE'S REAL SQL, AGAINST THE REAL SCHEMA.
  #
  # agent-memory.ts is unit-tested with a stubbed pool whose queryObject accepts any
  # string. That is fine for control flow and useless for SQL: the first version of the
  # writeback inserted into a `detail` column that does not exist (the schema has
  # `payload`), and the suite passed because the assertion was that the statement CONTAINED
  # "INSERT INTO agent_memory_audit_events" - true of a statement Postgres rejects. Every
  # writeback would have failed, after committing two rows.
  #
  # So the statements are executed here against the schema that ships beside them. The
  # throwaway DB is already up; this costs one psql call. thought_id is left NULL (the
  # column is nullable) so this needs no embedding lane.
  $amSql = @"
BEGIN;
INSERT INTO agent_memories (
  thought_id, workspace_id, project_id, channel_kind, channel_id,
  summary, content, memory_type, visibility, review_status,
  lifecycle_status, provenance_status, can_use_as_evidence,
  requires_user_confirmation, idempotency_key, content_hash, metadata
) VALUES (
  NULL, 'ws-harness', NULL, NULL, NULL,
  'harness summary', 'harness content', 'lesson', 'project', 'evidence_only',
  'active', 'generated', true,
  true, 'harness-key', agent_memory_hash_text('harness content'), '{}'::jsonb
);
INSERT INTO agent_memory_audit_events (memory_id, workspace_id, event_type, payload)
  SELECT id, 'ws-harness', 'memory_written', jsonb_build_object('via', 'harness')
    FROM agent_memories WHERE workspace_id = 'ws-harness';
SELECT 'am_sql_ok', count(*) FROM agent_memory_audit_events WHERE workspace_id = 'ws-harness';
ROLLBACK;
"@
  $amOut = docker exec -i ob-initdb-test psql -U postgres -d openbrain -tA -v ON_ERROR_STOP=1 -c $amSql 2>&1
  if ($LASTEXITCODE -eq 0 -and ($amOut | Out-String) -match "am_sql_ok\|1") {
    Pass "agent-memory writeback SQL executes against the real schema"
  } else {
    Write-Host ($amOut | Out-String) -ForegroundColor Red
    Fail "agent-memory writeback SQL rejected by the real schema"
  }

  # The idempotency index must be scoped PER WORKSPACE. Globally unique, two tenants using
  # the same obvious key collide and the loser is handed the winner's memory.
  $idxOut = docker exec ob-initdb-test psql -U postgres -d openbrain -tA -c @"
SELECT 'ws_scoped', count(*) FROM pg_indexes
 WHERE indexname = 'idx_agent_memories_ws_idempotency_key';
SELECT 'global_gone', count(*) FROM pg_indexes
 WHERE indexname = 'idx_agent_memories_idempotency_key';
"@
  $idxTxt = ($idxOut | Out-String)
  if ($idxTxt -match "ws_scoped\|1" -and $idxTxt -match "global_gone\|0") {
    Pass "idempotency_key is unique per workspace, not globally"
  } else {
    Write-Host $idxTxt -ForegroundColor Red
    Fail "idempotency_key index scope is wrong"
  }

  docker rm -f ob-initdb-test 2>$null | Out-Null
}

function Invoke-E2E {
  Section "Build images (compiles the Quartz overlay components)"
  docker compose -f $ob1 build openbrain-workbench openbrain-extract openbrain-wiki-viewer
  if ($LASTEXITCODE -eq 0) { Pass "image build" } else { Fail "image build"; return }

  Section "Throwaway stack (project ob-test, fresh volumes)"
  docker compose -p ob-test -f $ob1 up -d openbrain-db openbrain-extract openbrain-workbench
  Start-Sleep 25

  Section "Smoke /health"
  try { (Invoke-RestMethod http://127.0.0.1:8815/health) | ConvertTo-Json -Compress | Write-Host; Pass "extract /health" }
  catch { Fail "extract /health: $_" }
  try { (Invoke-RestMethod http://127.0.0.1:8814/health) | ConvertTo-Json -Compress | Write-Host; Pass "workbench /health" }
  catch { Fail "workbench /health: $_" }

  Section "Teardown (removes throwaway volumes)"
  docker compose -p ob-test -f $ob1 down -v
}

if ($Phase -in @("unit", "all")) { Invoke-Unit }
if ($Phase -in @("e2e", "all")) { Invoke-E2E }

Write-Host "`n=================================" -ForegroundColor Cyan
if ($fails -eq 0) { Write-Host "ALL OFFLINE CHECKS PASSED" -ForegroundColor Green }
else { Write-Host "$fails CHECK(S) FAILED" -ForegroundColor Red }
exit $fails
