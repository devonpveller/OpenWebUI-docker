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
. (Join-Path $PSScriptRoot "lib\ob-initdb.ps1")
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
  # DEFAULT recall. Two local instances of the opposite were found and reproduced against
  # a real database: review_status defaulting to 'pending' (which the gate excludes), and
  # visibility 'project' with a NULL project_id. Both look correct on each side alone.
  # GLOBBED, not listed. The file list here named four files and was already stale when the
  # review modules landed - two new suites would have sat in the repo, green on demand and
  # run by nothing. Same class as the hardcoded initdb chain this harness caught: a list
  # that has to be edited to stay true eventually stops being true. If the glob matches
  # nothing it stays literal and deno errors, so an empty match fails rather than passes.
  #
  # --allow-read IS LOAD-BEARING, and its absence was a silent no-op for weeks. Two suites
  # here are CROSS-READER tests: they read another file (the .sql that owns the memory_type
  # CHECK; the subsystem's own sources, for the exposure-plane completeness gate) and assert
  # the two agree. `deno test` without --allow-read cannot open a sibling file at all - it
  # raises NotCapable - and the memory_type test caught that in a try/catch and returned
  # early, so it PASSED while comparing nothing. Verified 2026-08-30 by running it both
  # ways: with the flag it compares 9 values, without it compares none. The suites now fail
  # closed on an unreadable file, and this flag is what lets them read one.
  # THE MOUNT IS THE WHOLE OB1 TREE, not just the source directory, and that is load-bearing
  # for the same reason as the flag. Two suites here read files OUTSIDE their own folder -
  # the memory_type test reads ../../docker/init-agent-memory*.sql, the exposure-plane
  # completeness gate reads its sibling sources - and with only the source directory mounted
  # the .sql files are not in the container at all. So even with --allow-read the cross-reader
  # comparison had nothing to compare against (verified 2026-08-30: it fails NotFound under
  # the old mount and compares 9 memory_type values under this one). Mounting OB1 at /ob1 and
  # working from the same relative position as the repo makes the container's paths the
  # repo's paths, so a test that passes here passes for the same reason it passes locally.
  docker run --rm -v "${rootFwd}/OB1:/ob1:ro" `
    -w /ob1/integrations/kubernetes-deployment denoland/deno:2.3.3 `
    sh -c "deno check agent-memory*.ts index.ts && deno test --allow-read agent-memory*.test.ts"
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
  # Derived by scripts/checks/lib/ob-initdb.ps1, which the agent-memory smoke script also
  # uses. It was extracted rather than copied precisely because THIS section is where two
  # stale copies were already caught (the hardcoded list, and the preview compose).
  $map = Get-ObInitChain -ComposePath $ob1
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

  # THE PREVIEW COMPOSE MUST CARRY THE SAME CHAIN.
  # It kept its own hand-maintained copy and drifted EIGHT migrations behind - a preview
  # database came up with no agent-memory plane, no wiki_pages and no claims layer, and
  # nothing said so. Same class as the stale hardcoded list this section already fixed,
  # one file over.
  $previewPath = "OB1/docker/docker-compose.preview.yml"
  if (Test-Path $previewPath) {
    $previewSources = @(([regex]'\./(init[a-z0-9.\-]*\.sql):/docker-entrypoint-initdb\.d/').Matches((Get-Content -Raw $previewPath)) |
                        ForEach-Object { $_.Groups[1].Value })
    $mainSources = @($map | ForEach-Object { $_[0] })
    $missingFromPreview = @($mainSources | Where-Object { $previewSources -notcontains $_ })
    $extraInPreview = @($previewSources | Where-Object { $mainSources -notcontains $_ })
    if ($missingFromPreview.Count -or $extraInPreview.Count) {
      Fail ("preview compose chain differs from production" +
            $(if ($missingFromPreview.Count) { " - missing: " + ($missingFromPreview -join ", ") }) +
            $(if ($extraInPreview.Count) { " - extra: " + ($extraInPreview -join ", ") }))
    } else { Pass "preview compose carries the same initdb chain as production" }
  }

  $null = Copy-ObInitChain -Chain $map -SourceDir (Join-Path $PWD "OB1\docker") -TargetDir $tmp
  # The container start and the WAIT-FOR-THE-RIGHT-MARKER logic live in the lib; the note
  # about "ready to accept connections" appearing twice is there with them.
  $ready = Start-ObInitdb -Name "ob-initdb-test" -InitDir $tmp
  if ($ready) { Pass "initdb finished (entrypoint reported init process complete)" }
  else { Fail "initdb did not complete within 180s - results below are not trustworthy" }

  $errLines = Get-ObInitdbErrors -Name "ob-initdb-test"
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
-- The RECALL side, same reasoning: its joins, its trace insert and its item insert are
-- all stubbed in the unit tests, so only a real database can say whether the columns
-- exist. The vector operator is exercised too, since that is where a dimension or
-- operator-class mistake would surface.
INSERT INTO agent_memory_recall_traces
  (workspace_id, project_id, query, schema_version, request_payload, response_policy)
VALUES ('ws-harness', NULL, 'harness query', 'openbrain.agent_memory.recall.v1',
        jsonb_build_object('limit', 8), jsonb_build_object('returned', 0));
INSERT INTO agent_memory_recall_items
  (trace_id, memory_id, rank, similarity, use_policy_snapshot)
  SELECT t.id, m.id, 1, 0.9000, jsonb_build_object('can_use_as_evidence', true)
    FROM agent_memory_recall_traces t, agent_memories m
   WHERE t.workspace_id = 'ws-harness' AND m.workspace_id = 'ws-harness';
SELECT 'am_recall_join_ok', count(*) FROM agent_memories am
  JOIN thoughts t ON t.id = am.thought_id
 WHERE am.workspace_id = 'ws-harness' AND am.lifecycle_status = 'active';
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

  # memory_type='check' (U3). The finding->durable-check pipeline writes this type, and
  # it did not exist: the vendored schema permits eight values and a writeback with
  # 'check' is rejected by the CHECK at runtime and by NOTHING at test time when the pool
  # is stubbed. Both directions are asserted - the new value is accepted AND an invented
  # one is still refused, because a migration that widened the constraint to anything
  # would look identical from the accepting side alone.
  $ckTypeSql = @"
BEGIN;
INSERT INTO agent_memories (workspace_id, summary, content, memory_type)
VALUES ('ws-checktype', 'a durable check', 'pytest -q must stay green', 'check');
SELECT 'check_type_ok', count(*) FROM agent_memories
 WHERE workspace_id = 'ws-checktype' AND memory_type = 'check';
ROLLBACK;
"@
  $ckTypeOut = docker exec -i ob-initdb-test psql -U postgres -d openbrain -tA -v ON_ERROR_STOP=1 -c $ckTypeSql 2>&1
  if ($LASTEXITCODE -eq 0 -and ($ckTypeOut | Out-String) -match "check_type_ok\|1") {
    Pass "memory_type 'check' is accepted by the real schema (U3)"
  } else {
    Write-Host ($ckTypeOut | Out-String) -ForegroundColor Red
    Fail "memory_type 'check' was rejected - the U3 migration did not reach this volume"
  }

  $badTypeSql = @"
BEGIN;
INSERT INTO agent_memories (workspace_id, summary, content, memory_type)
VALUES ('ws-badtype', 's', 'c', 'not_a_real_type');
ROLLBACK;
"@
  $badTypeOut = docker exec -i ob-initdb-test psql -U postgres -d openbrain -tA -v ON_ERROR_STOP=1 -c $badTypeSql 2>&1
  if ($LASTEXITCODE -ne 0 -and ($badTypeOut | Out-String) -match "violates check constraint") {
    Pass "an invented memory_type is still refused (the CHECK was widened, not removed)"
  } else {
    Write-Host ($badTypeOut | Out-String) -ForegroundColor Red
    Fail "an invented memory_type was ACCEPTED - the constraint is gone, not widened"
  }

  # §1.1 EXPOSURE, against the real schema. The recall filter reads exposure out of a JSONB
  # key with COALESCE to 'personal', and two things there can only be answered by Postgres:
  # that the expression is valid at all, and that a row written BEFORE exposure shipped -
  # no metadata.exposure - reads as personal rather than dropping out of `= ANY` invisibly.
  # A memory that silently vanishes from every recall is the failure this plane exists to
  # prevent, and it would look identical to a memory that was never written.
  $expSql = @"
BEGIN;
INSERT INTO agent_memories (workspace_id, summary, content, memory_type, visibility, metadata)
VALUES ('ws-exp', 'legacy', 'written before exposure existed', 'lesson', 'project', '{}'::jsonb);
INSERT INTO agent_memories (workspace_id, summary, content, memory_type, visibility, metadata)
-- jsonb_build_object, NOT a JSON literal: PowerShell strips embedded double quotes when
-- it passes an argument to a native command, so '{"exposure":"ops"}' arrives as
-- {exposure:ops} and Postgres rejects it. The writeback block above uses the same function
-- for the same reason.
VALUES ('ws-exp', 'ops row', 'written after', 'lesson', 'project', jsonb_build_object('exposure', 'ops'));
SELECT 'legacy_is_personal', count(*) FROM agent_memories am
 WHERE am.workspace_id = 'ws-exp'
   AND COALESCE(am.metadata->>'exposure', 'personal') = ANY(ARRAY['personal']);
SELECT 'ops_visible', count(*) FROM agent_memories am
 WHERE am.workspace_id = 'ws-exp'
   AND COALESCE(am.metadata->>'exposure', 'personal') = ANY(ARRAY['ops']);
ROLLBACK;
"@
  $expOut = docker exec -i ob-initdb-test psql -U postgres -d openbrain -tA -v ON_ERROR_STOP=1 -c $expSql 2>&1
  $expTxt = ($expOut | Out-String)
  if ($LASTEXITCODE -eq 0 -and $expTxt -match "legacy_is_personal\|1" -and $expTxt -match "ops_visible\|1") {
    Pass "exposure filter works on the real schema, and a pre-exposure row reads as personal"
  } else {
    Write-Host $expTxt -ForegroundColor Red
    Fail "the exposure filter did not behave against the real schema"
  }

  # THE REVIEW DOOR'S SQL, against the real schema (memory-plane Phase 1.4).
  #
  # Same reasoning as the writeback block above, and the same failure it is guarding: the
  # ops tests stub the pool, so a column that does not exist or an enum value the CHECK
  # refuses passes every one of them and fails on first real use. Three things here can
  # only be answered by Postgres:
  #   - review_status 'confirmed' and lifecycle 'rejected'/'superseded'/'disputed' are
  #     accepted by their CHECK constraints;
  #   - 'memory_confirmed' is accepted by the audit event_type CHECK, and actor_kind 'user'
  #     by its own;
  #   - setting provenance_status='user_confirmed' does not trip the
  #     can_use_as_instruction CHECK (init-agent-memory.sql:94), which is the constraint the
  #     confirm path moves closest to.
  $revSql = @"
BEGIN;
INSERT INTO agent_memories (
  thought_id, workspace_id, project_id, summary, content, memory_type, visibility,
  review_status, lifecycle_status, provenance_status, can_use_as_evidence,
  requires_user_confirmation, content_hash, metadata
) VALUES (
  NULL, 'ws-review', 'p-review', 'review summary', 'review content', 'lesson', 'project',
  'evidence_only', 'active', 'generated', true, true,
  agent_memory_hash_text('review content'), '{}'::jsonb
);
-- CONFIRM, exactly as agent-memory-ops.ts builds it.
UPDATE agent_memories
   SET review_status = 'confirmed', updated_at = now(),
       provenance_status = 'user_confirmed', last_confirmed_at = now(),
       requires_user_confirmation = false
 WHERE workspace_id = 'ws-review';
INSERT INTO agent_memory_audit_events
  (memory_id, workspace_id, project_id, event_type, actor_kind, actor_label, payload)
  SELECT id, 'ws-review', 'p-review', 'memory_confirmed', 'user', 'harness',
         jsonb_build_object('from', 'evidence_only', 'to', 'confirmed')
    FROM agent_memories WHERE workspace_id = 'ws-review';
-- The other three lifecycle targets must each be accepted by their CHECK.
UPDATE agent_memories SET review_status = 'rejected',  lifecycle_status = 'rejected'   WHERE workspace_id = 'ws-review';
UPDATE agent_memories SET review_status = 'merged',    lifecycle_status = 'superseded' WHERE workspace_id = 'ws-review';
UPDATE agent_memories SET review_status = 'restricted', lifecycle_status = 'disputed'  WHERE workspace_id = 'ws-review';
INSERT INTO agent_memory_audit_events (memory_id, workspace_id, event_type, actor_kind, actor_label, payload)
  SELECT id, 'ws-review', 'memory_rejected',   'user', 'harness', '{}'::jsonb FROM agent_memories WHERE workspace_id = 'ws-review';
INSERT INTO agent_memory_audit_events (memory_id, workspace_id, event_type, actor_kind, actor_label, payload)
  SELECT id, 'ws-review', 'memory_superseded', 'user', 'harness', '{}'::jsonb FROM agent_memories WHERE workspace_id = 'ws-review';
INSERT INTO agent_memory_audit_events (memory_id, workspace_id, event_type, actor_kind, actor_label, payload)
  SELECT id, 'ws-review', 'memory_disputed',   'user', 'harness', '{}'::jsonb FROM agent_memories WHERE workspace_id = 'ws-review';
-- THE REVIEW-ACTIONS TABLE. It exists to record who changed a memory's standing and what
-- it looked like before; an earlier implementation wrote only audit events and never
-- touched it. All TEN actions are exercised, because a CHECK rejects by value and a
-- stubbed pool cannot see that.
INSERT INTO agent_memory_review_actions (memory_id, action, actor_label, notes, before, after)
  SELECT id, a.action, 'harness', 'exercising every action',
         jsonb_build_object('review_status', 'pending'),
         jsonb_build_object('review_status', 'confirmed')
    FROM agent_memories,
         unnest(ARRAY['confirm','edit','evidence_only','restrict_scope','promote_exposure',
                      'mark_stale','merge','reject','dispute','supersede']) AS a(action)
   WHERE workspace_id = 'ws-review';
-- promote_exposure is the MIGRATED tenth (init-agent-memory-promote-exposure.sql). If that
-- migration ever fails to reach a fresh volume, this insert is what says so - the CHECK
-- would reject the value and nothing else here would notice.
SELECT 'am_actions_ok', count(*) FROM agent_memory_review_actions ra
  JOIN agent_memories am ON am.id = ra.memory_id WHERE am.workspace_id = 'ws-review';
-- And the exposure merge the promote path performs, against the real column type.
UPDATE agent_memories
   SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('exposure', 'ops')
 WHERE workspace_id = 'ws-review';
SELECT 'am_promote_ok', count(*) FROM agent_memories
 WHERE workspace_id = 'ws-review' AND metadata->>'exposure' = 'ops';
SELECT 'am_review_ok', count(*) FROM agent_memory_audit_events WHERE workspace_id = 'ws-review';
ROLLBACK;
"@
  $revOut = docker exec -i ob-initdb-test psql -U postgres -d openbrain -tA -v ON_ERROR_STOP=1 -c $revSql 2>&1
  $revTxt = ($revOut | Out-String)
  if ($LASTEXITCODE -eq 0 -and $revTxt -match "am_review_ok\|4" -and
      $revTxt -match "am_actions_ok\|10" -and $revTxt -match "am_promote_ok\|1") {
    Pass "agent-memory REVIEW SQL executes against the real schema (4 audit events, all 10 actions, promote merge)"
  } else {
    Write-Host ($revOut | Out-String) -ForegroundColor Red
    Fail "agent-memory review SQL rejected by the real schema"
  }

  # AND THE CONSTRAINT THAT MATTERS MOST MUST STILL BITE. Confirming sets provenance to
  # 'user_confirmed', which is one of the two values that make instruction-grade LEGAL. If
  # the CHECK were ever dropped, nothing else in this repo would notice - so prove it still
  # refuses the combination the review door must never be able to produce.
  $ckSql = @"
BEGIN;
INSERT INTO agent_memories (workspace_id, summary, content, memory_type, provenance_status, can_use_as_instruction)
VALUES ('ws-ck', 's', 'c', 'lesson', 'generated', true);
ROLLBACK;
"@
  $ckOut = docker exec -i ob-initdb-test psql -U postgres -d openbrain -tA -v ON_ERROR_STOP=1 -c $ckSql 2>&1
  if ($LASTEXITCODE -ne 0 -and ($ckOut | Out-String) -match "violates check constraint") {
    Pass "instruction-grade is still REFUSED for generated provenance (the CHECK bites)"
  } else {
    Write-Host ($ckOut | Out-String) -ForegroundColor Red
    Fail "a generated memory was allowed to claim can_use_as_instruction"
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
