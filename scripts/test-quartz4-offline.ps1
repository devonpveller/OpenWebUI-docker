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
  docker compose -f $ob1 config -q; if ($?) { Pass "docker compose config" } else { Fail "compose config" }
  foreach ($f in @(
      "OB1/recipes/_shared/slug.mjs",
      "OB1/recipes/entity-wiki/generate-wiki.mjs",
      "OB1/docker/wiki-service/wiki-service.mjs",
      "OB1/recipes/wiki-synthesis/scripts/synthesize-notebooks.mjs")) {
    node --check $f; if ($?) { Pass "node --check $f" } else { Fail "node --check $f" }
  }
  python -m py_compile OB1/docker/extract/app.py; if ($?) { Pass "py_compile extract/app.py" } else { Fail "py_compile" }

  Section "Node unit tests (slug + citation rewrite)"
  node --test OB1/recipes/_shared/slug.test.mjs OB1/recipes/entity-wiki/generate-wiki.test.mjs
  if ($?) { Pass "node --test" } else { Fail "node --test" }

  Section "Deno: type-check + unit tests (workbench)"
  docker run --rm -v "${rootFwd}/OB1/docker/workbench:/app" -v "${rootFwd}/OB1/recipes:/recipes:ro" `
    -w /app denoland/deno:2.3.3 sh -c "deno check src/main.ts && deno test src/util/paths_test.ts src/util/chunk_test.ts"
  if ($?) { Pass "deno check + deno test" } else { Fail "deno check/test" }

  Section "Caddy validate (portal route)"
  docker run --rm -e PUBLIC_DOMAIN=example.com -e ACME_EMAIL=a@b.c -e WORKBENCH_KEY=k `
    -v "${rootFwd}/config/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.8.4-alpine `
    caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile
  if ($?) { Pass "caddy validate" } else { Fail "caddy validate" }

  Section "Schema migrations on a throwaway pgvector (fresh volume)"
  $tmp = (Join-Path $env:TEMP "ob-initdb") -replace '\\', '/'
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory $tmp -Force | Out-Null
  $map = @(
    @("init.sql", "10-init.sql"), @("init-extensions.sql", "20-init-extensions.sql"),
    @("init-sources.sql", "30-init-sources.sql"), @("init-graph.sql", "40-init-graph.sql"),
    @("init-grants.sql", "50-init-grants.sql"), @("init-source-graph.sql", "60-init-source-graph.sql"),
    @("init-threads.sql", "70-init-threads.sql"), @("init-threads-slug.sql", "72-init-threads-slug.sql"),
    @("init-source-revisions.sql", "80-init-source-revisions.sql"), @("init-source-retract.sql", "82-init-source-retract.sql"),
    @("init-content-types.sql", "84-init-content-types.sql"), @("init-source-chunks.sql", "86-init-source-chunks.sql"),
    @("init-import-jobs.sql", "88-init-import-jobs.sql")
  )
  foreach ($m in $map) { Copy-Item "OB1/docker/$($m[0])" (Join-Path $tmp $m[1]) }
  docker rm -f ob-initdb-test 2>$null | Out-Null
  docker run -d --name ob-initdb-test -e POSTGRES_DB=openbrain -e POSTGRES_USER=postgres `
    -e POSTGRES_PASSWORD=test -v "${tmp}:/docker-entrypoint-initdb.d:ro" pgvector/pgvector:pg16 | Out-Null
  Start-Sleep 18
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
UNION ALL SELECT 'import_jobs',count(*) FROM information_schema.tables WHERE table_name='import_jobs';
"@
  Write-Host $verify
  docker rm -f ob-initdb-test 2>$null | Out-Null
}

function Invoke-E2E {
  Section "Build images (compiles the Quartz overlay components)"
  docker compose -f $ob1 build openbrain-workbench openbrain-extract openbrain-wiki-viewer
  if ($?) { Pass "image build" } else { Fail "image build"; return }

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
