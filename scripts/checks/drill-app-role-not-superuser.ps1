# drill-app-role-not-superuser.ps1 - H1 (DFU section C.9) executable proof.
#
# THE CLAIM UNDER TEST, quoted from the plan:
#   "a run that opens a connection as the app role and shows a personal row is invisible
#    WITHOUT any SET ROLE"
#
# Everything here runs on a THROWAWAY database built from the real initdb chain, on its own
# docker network, never attached to an ai-stack_* anchor network, seeded with SYNTHETIC
# fixtures only. It never touches openbrain-db. There is no -Live switch and there will not
# be one: the personal plane is class-4 and a drill that can point at production is a drill
# that eventually does.
#
# RED IS PART OF THE PROOF. A test that only shows "the app role sees nothing" is also passed
# by a broken connection, a wrong table name and an empty database. Every GREEN probe here has
# a named RED twin run against the same fixtures on the same database in the same run.
#
# Exit 0 = every probe as expected. Exit 1 = a probe disagreed. Exit 2 = the harness itself
# could not run (image pull, initdb failure) - which is NOT a pass.

[CmdletBinding()]
param(
    [string]$Id = "u8h1",
    [switch]$KeepContainer
)

# "Continue", not "Stop": ob-initdb.ps1 shells out to `docker rm -f` on a name that does not
# exist yet, and under Stop that NativeCommandError aborts the run before the database is
# ever built. Failure here is judged by the probes, not by a stray stderr line.
$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest

$repo      = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$obDocker  = Join-Path $repo "OB1\docker"
$compose   = Join-Path $obDocker "docker-compose.yml"
$lib       = Join-Path $PSScriptRoot "lib\ob-initdb.ps1"
. $lib

$dbName  = "wt-$Id-h1db"
$netName = "wt-$Id-h1net"
$initDir = Join-Path $env:TEMP "wt-$Id-h1-initdb"
$appPw   = "h1-drill-app-pw"
$memPw   = "h1-drill-mem-pw"
$tenant  = "H1-DRILL-SYNTHETIC-TENANT"

$script:fail = 0
$script:probeNo = 0

function Say([string]$m) { Write-Host $m }
function Head([string]$m) { Write-Host ""; Write-Host "== $m" }

# Run SQL as a given role over TCP inside the container. Returns a PSCustomObject with
# Out (trimmed stdout+stderr) and Code (psql exit code). Password auth is forced by -h so
# that "connected as ob_app" cannot silently be "connected over the trust socket as postgres".
function Sql {
    param(
        [Parameter(Mandatory)][string]$Role,
        [Parameter(Mandatory)][string]$Query,
        [string]$Password = ""
    )
    $dargs = @("exec", "-i")
    if ($Password -ne "") { $dargs += @("-e", "PGPASSWORD=$Password") }
    $dargs += @($dbName, "psql", "-h", "127.0.0.1", "-U", $Role, "-d", "openbrain",
               "-v", "ON_ERROR_STOP=1", "-At", "-q", "-F", "|")
    $out = ($Query | & docker @dargs 2>&1 | Out-String)
    return [PSCustomObject]@{ Out = $out.Trim(); Code = $LASTEXITCODE }
}

function Probe {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Expect,
        [Parameter(Mandatory)]$Actual
    )
    $script:probeNo++
    $a = ($Actual -replace "\s+", " ").Trim()
    $short = if ($a.Length -gt 110) { $a.Substring(0,110) + " ..." } else { $a }
    if ($a -eq $Expect) {
        Say ("  [{0,2}] PASS  {1}  -> {2}" -f $script:probeNo, $Name, $short)
    } else {
        Say ("  [{0,2}] FAIL  {1}" -f $script:probeNo, $Name)
        Say ("        expected: {0}" -f $Expect)
        Say ("        actual  : {0}" -f $a)
        $script:fail++
    }
}

function ProbeMatch {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Pattern,
        [Parameter(Mandatory)]$Actual
    )
    $script:probeNo++
    $a = ($Actual -replace "\s+", " ").Trim()
    $short = if ($a.Length -gt 110) { $a.Substring(0,110) + " ..." } else { $a }
    if ($a -match $Pattern) {
        Say ("  [{0,2}] PASS  {1}  -> {2}" -f $script:probeNo, $Name, $short)
    } else {
        Say ("  [{0,2}] FAIL  {1}" -f $script:probeNo, $Name)
        Say ("        expected to match: {0}" -f $Pattern)
        Say ("        actual           : {0}" -f $a)
        $script:fail++
    }
}

function Cleanup {
    if ($KeepContainer) { Say "  (container kept: $dbName)"; return }
    $null = cmd /c "docker rm -f $dbName 2>nul"
    $null = cmd /c "docker network rm $netName 2>nul"
}

trap { Say "HARNESS ERROR: $_"; Cleanup; exit 2 }

Say "H1 drill - no application connects as a superuser"
Say "repo: $repo"

# ------------------------------------------------------------------------------------------
# 0. Stage the chain
# ------------------------------------------------------------------------------------------
Head "0. initdb chain"
$chain = @(Get-ObInitChain -ComposePath $compose)
Say "  compose mounts $($chain.Count) init files"
$staged = Copy-ObInitChain -Chain $chain -SourceDir $obDocker -TargetDir $initDir
Say "  staged $staged"
if ($staged -ne $chain.Count) {
    Say "  ABORT: $($chain.Count) mounted but $staged staged - a file compose names is missing"
    Cleanup; exit 2
}

# H1's own two files. If compose already mounts them the loop above staged them; if not, this
# drill stages them itself and SAYS SO, because an unmounted migration is a half-done
# two-place mechanism and hiding that is the failure this project keeps finding.
$h1sql = Join-Path $obDocker "init-app-role.sql"
$h1sh  = Join-Path $obDocker "init-app-role-passwords.sh"
$mounted = @($chain | Where-Object { $_[0] -eq "init-app-role.sql" }).Count -gt 0
if ($mounted) {
    Say "  init-app-role.sql: MOUNTED BY COMPOSE (promotion step 1 has landed)"
} else {
    Copy-Item $h1sql (Join-Path $initDir "210-init-app-role.sql")
    Say "  init-app-role.sql: NOT mounted by compose - staged by the drill at 210-."
    Say "                     The migration is validated but NOT deployed. That is the"
    Say "                     gated promotion, not this drill's business."
}
Copy-Item $h1sh (Join-Path $initDir "215-init-app-role-passwords.sh")
# CRLF in a mounted .sh is a silent no-run under the postgres entrypoint.
$shBytes = [IO.File]::ReadAllBytes((Join-Path $initDir "215-init-app-role-passwords.sh"))
if ($shBytes -contains 13) {
    Say "  ABORT: 215-init-app-role-passwords.sh contains CR - the entrypoint will not run it"
    Cleanup; exit 2
}

# ------------------------------------------------------------------------------------------
# 1. Start the throwaway
# ------------------------------------------------------------------------------------------
Head "1. throwaway database"
$null = cmd /c "docker rm -f $dbName 2>nul"
$null = cmd /c "docker network rm $netName 2>nul"
$null = cmd /c "docker network create $netName"
$ok = Start-ObInitdb -Name $dbName -InitDir $initDir -TimeoutSec 300 -DockerArgs @(
    "--network", $netName,
    "-e", "OB_APP_PASSWORD=$appPw",
    "-e", "OB_APP_MEMORY_PASSWORD=$memPw"
)
if (-not $ok) {
    Say "  ABORT: initdb did not complete"
    docker logs $dbName 2>&1 | Select-Object -Last 40 | ForEach-Object { Say "    $_" }
    Cleanup; exit 2
}
# @() because `return @(...)` from a function unrolls: one error line comes back as a
# bare MatchInfo and zero comes back as $null, and $null.Count throws under StrictMode.
$errs = @(Get-ObInitdbErrors -Name $dbName)
if ($errs.Count -gt 0) {
    Say "  ABORT: initdb reported $($errs.Count) error line(s)"
    $errs | Select-Object -First 20 | ForEach-Object { Say "    $_" }
    Cleanup; exit 2
}
Say "  up, chain clean, network $netName (no ai-stack_* attachment)"

# ------------------------------------------------------------------------------------------
# 2. Synthetic fixtures - seeded as the superuser so RLS cannot silently drop them
# ------------------------------------------------------------------------------------------
Head "2. synthetic fixtures"
$seed = @"
INSERT INTO public.thoughts (content, metadata, user_id) VALUES
  ('H1-DRILL ops thought',      '{"exposure":"ops"}'::jsonb,      NULL),
  ('H1-DRILL personal thought', '{"exposure":"personal"}'::jsonb, '$tenant');

INSERT INTO public.agent_memories
  (workspace_id, memory_type, summary, content, metadata, user_id) VALUES
  ('H1-DRILL', 'lesson', 'ops memory',      'ops body',      '{"exposure":"ops"}'::jsonb,      NULL),
  ('H1-DRILL', 'lesson', 'personal memory', 'personal body', '{"exposure":"personal"}'::jsonb, '$tenant');

-- A view fixture with teeth: an idea whose current revision hangs off the PERSONAL thought.
-- ideas_owed_research JOINs idea_revisions, whose policy is the plane predicate, so this row
-- is exactly what a view running as its superuser owner leaks.
WITH i AS (
  INSERT INTO public.ideas (title, summary, status, current_revision)
  VALUES ('H1-DRILL personal idea', 'seeded', 'new', 1) RETURNING id
)
INSERT INTO public.idea_revisions (idea_id, revision, summary, thought_id, research_job_id)
SELECT i.id, 1, 'seeded', t.id, NULL
  FROM i, public.thoughts t WHERE t.content = 'H1-DRILL personal thought';

-- The ops twin, so the WRITE probes in section 6b measure the GRANT and not the policy.
WITH i AS (
  INSERT INTO public.ideas (title, summary, status, current_revision)
  VALUES ('H1-DRILL ops idea', 'seeded-ops', 'new', 1) RETURNING id
)
INSERT INTO public.idea_revisions (idea_id, revision, summary, thought_id, research_job_id)
SELECT i.id, 1, 'seeded-ops', t.id, NULL
  FROM i, public.thoughts t WHERE t.content = 'H1-DRILL ops thought';

-- An extension-table fixture. WITHOUT THIS ROW the probe in section 7 reads 0 from an
-- EMPTY table and "the app role cannot see it" is indistinguishable from "there is nothing
-- to see" - the exact shape of check that passes while checking nothing.
INSERT INTO public.recipes (user_id, name)
VALUES ('00000000-0000-0000-0000-0000000000d1'::uuid, 'H1-DRILL recipe');

SELECT 'seeded|' ||
  (SELECT count(*) FROM public.thoughts       WHERE content LIKE 'H1-DRILL%') || '|' ||
  (SELECT count(*) FROM public.agent_memories WHERE workspace_id = 'H1-DRILL') || '|' ||
  (SELECT count(*) FROM public.idea_revisions WHERE summary LIKE 'seeded%') || '|' ||
  (SELECT count(*) FROM public.recipes        WHERE name = 'H1-DRILL recipe');
"@
$r = Sql -Role "postgres" -Password "test" -Query $seed
Probe "C0  fixtures seeded (2 thoughts, 2 memories, 2 revisions, 1 recipe)" "seeded|2|2|2|1" $r.Out

# ------------------------------------------------------------------------------------------
# 3. THE RED - the same queries as the superuser
# ------------------------------------------------------------------------------------------
Head "3. RED - as postgres, the personal rows ARE visible"
$r = Sql -Role "postgres" -Password "test" -Query @"
SELECT rolsuper::text || '|' || rolbypassrls::text FROM pg_roles WHERE rolname = current_user;
"@
Probe "R1  postgres is rolsuper|rolbypassrls" "true|true" $r.Out

$countQ = @"
SELECT (SELECT count(*) FROM public.thoughts       WHERE metadata->>'exposure' = 'personal' AND content LIKE 'H1-DRILL%')
    || '|' ||
       (SELECT count(*) FROM public.agent_memories WHERE metadata->>'exposure' = 'personal' AND workspace_id = 'H1-DRILL');
"@
$r = Sql -Role "postgres" -Password "test" -Query $countQ
Probe "R2  personal thought and memory visible to the superuser" "1|1" $r.Out

# ------------------------------------------------------------------------------------------
# 4. THE HEADLINE - as the app role, no SET ROLE anywhere
# ------------------------------------------------------------------------------------------
Head "4. GREEN - as ob_app, with no SET ROLE at all"
$r = Sql -Role "ob_app" -Password $appPw -Query "SELECT current_user || '|' || rolsuper::text || '|' || rolbypassrls::text FROM pg_roles WHERE rolname = current_user;"
Probe "G1  connected as ob_app, not super, not bypassrls" "ob_app|false|false" $r.Out

$r = Sql -Role "ob_app" -Password $appPw -Query $countQ
Probe "G2  personal thought and memory INVISIBLE" "0|0" $r.Out

$opsQ = @"
SELECT (SELECT count(*) FROM public.thoughts       WHERE metadata->>'exposure' = 'ops' AND content LIKE 'H1-DRILL%')
    || '|' ||
       (SELECT count(*) FROM public.agent_memories WHERE metadata->>'exposure' = 'ops' AND workspace_id = 'H1-DRILL');
"@
$r = Sql -Role "ob_app" -Password $appPw -Query $opsQ
Probe "G3  POSITIVE CONTROL: the ops twins ARE visible to ob_app" "1|1" $r.Out

$r = Sql -Role "ob_app" -Password $appPw -Query @"
INSERT INTO public.thoughts (content, metadata) VALUES ('H1-DRILL ops write', '{"exposure":"ops"}'::jsonb);
SELECT 'wrote|' || count(*) FROM public.thoughts WHERE content = 'H1-DRILL ops write';
"@
Probe "G4  POSITIVE CONTROL: the ops WRITE path still works as ob_app" "wrote|1" $r.Out

$r = Sql -Role "ob_app" -Password $appPw -Query "SET ROLE ob_plane_personal; SELECT 1;"
ProbeMatch "G5  ob_app CANNOT switch into the personal plane" "permission denied to set role" $r.Out

# The SECURITY DEFINER trigger on `thoughts` has to keep firing for a non-superuser writer,
# or the entity pipeline stops without anyone noticing. The write in G4 is its trigger.
$r = Sql -Role "ob_app" -Password $appPw -Query @"
SELECT count(*)::text FROM public.entity_extraction_queue q
  JOIN public.thoughts t ON t.id = q.thought_id WHERE t.content = 'H1-DRILL ops write';
"@
Probe "G4b POSITIVE CONTROL: the definer trigger still queued the ops write" "1" $r.Out

# ------------------------------------------------------------------------------------------
# 5. ob_app_memory - bound by default, switchable on purpose
# ------------------------------------------------------------------------------------------
Head "5. GREEN - ob_app_memory is bound until it deliberately switches"
$r = Sql -Role "ob_app_memory" -Password $memPw -Query $countQ
Probe "G6  ob_app_memory sees NO personal row without SET ROLE" "0|0" $r.Out

$r = Sql -Role "ob_app_memory" -Password $memPw -Query @"
BEGIN;
SET LOCAL ROLE ob_plane_personal;
SET LOCAL ob.user_id = '$tenant';
$countQ
COMMIT;
"@
Probe "G7  ...and DOES see them after SET ROLE + tenant GUC (not merely blacked out)" "1|1" $r.Out

$r = Sql -Role "ob_app_memory" -Password $memPw -Query @"
BEGIN;
SET LOCAL ROLE ob_plane_personal;
SET LOCAL ob.user_id = 'SOMEBODY-ELSE';
$countQ
COMMIT;
"@
Probe "G8  ...and NOT another tenant's" "0|0" $r.Out

# THE WRITE DOOR. init-graph-plane-rls.sql (200-) revokes INSERT/UPDATE/DELETE on the whole
# agent-memory corpus and on idea_revisions FROM service_role - so on a chain-complete
# database the ops class is READ-ONLY there. init-app-role.sql section 2b reopens it for the
# two NAMED roles that were derived to need it. These four probes are the evidence that the
# reopening is exactly as wide as it was meant to be.
$r = Sql -Role "ob_app_memory" -Password $memPw -Query @"
INSERT INTO public.agent_memories (workspace_id, memory_type, summary, content, metadata)
VALUES ('H1-DRILL', 'lesson', 'ops write', 'body', '{"exposure":"ops"}'::jsonb);
SELECT 'wrote|' || count(*) FROM public.agent_memories WHERE summary = 'ops write';
"@
Probe "G8b ob_app_memory CAN write an ops memory (200's door reopened for it)" "wrote|1" $r.Out

# openbrain-mcp's writeback default is exposure='personal' (agent-memory-policy.ts:82). If
# that write merely vanished, a cutover would lose memories silently. It does not.
$r = Sql -Role "ob_app_memory" -Password $memPw -Query @"
INSERT INTO public.agent_memories (workspace_id, memory_type, summary, content, metadata, user_id)
VALUES ('H1-DRILL', 'lesson', 'unswitched personal write', 'body',
        '{"exposure":"personal"}'::jsonb, '$tenant');
"@
ProbeMatch "G8c a personal write WITHOUT SET ROLE is refused LOUDLY, not dropped" `
           "violates row-level security policy" $r.Out

# ...and the general ops class is still shut out, which is 200's decision and stays 200's.
$r = Sql -Role "postgres" -Password "test" -Query @"
BEGIN;
SET LOCAL ROLE service_role;
INSERT INTO public.agent_memories (workspace_id, memory_type, summary, content, metadata)
VALUES ('H1-DRILL', 'lesson', 'service_role write', 'body', '{"exposure":"ops"}'::jsonb);
COMMIT;
"@
ProbeMatch "G8d service_role STILL cannot write the corpus (200's door stays closed)" `
           "permission denied for table agent_memories" $r.Out

# openbrain-idea-refinery's only corpus write is UPDATE idea_revisions, and ob_app must keep it.
$r = Sql -Role "ob_app" -Password $appPw -Query @"
UPDATE public.idea_revisions SET summary = 'seeded-ops-updated' WHERE summary = 'seeded-ops';
SELECT 'updated|' || count(*) FROM public.idea_revisions WHERE summary = 'seeded-ops-updated';
"@
Probe "G8e ob_app keeps UPDATE idea_revisions (openbrain-idea-refinery's write path)" "updated|1" $r.Out

# ------------------------------------------------------------------------------------------
# 6. GRANTS - the other ways to the same rows
# ------------------------------------------------------------------------------------------
Head "6. the grant set - definer functions, views, sequences, PUBLIC"

# 6a. Views. RED first: reset the option this migration sets, and watch the leak reappear.
$viewQ = "SELECT count(*) FROM public.ideas_owed_research WHERE title = 'H1-DRILL personal idea';"
$r = Sql -Role "ob_app" -Password $appPw -Query $viewQ
Probe "G9  ideas_owed_research hides the personal-linked idea from ob_app" "0" $r.Out

Sql -Role "postgres" -Password "test" -Query "ALTER VIEW public.ideas_owed_research RESET (security_invoker);" | Out-Null
$r = Sql -Role "ob_app" -Password $appPw -Query $viewQ
Probe "R3  RED: with security_invoker reset, the SAME query LEAKS it" "1" $r.Out
Sql -Role "postgres" -Password "test" -Query "ALTER VIEW public.ideas_owed_research SET (security_invoker = true);" | Out-Null
$r = Sql -Role "ob_app" -Password $appPw -Query $viewQ
Probe "G10 ...and closes again when it is set back" "0" $r.Out

# 6b. Every view in the schema, not just the one with a fixture.
$r = Sql -Role "postgres" -Password "test" -Query @"
SELECT COALESCE(string_agg(c.relname, ',' ORDER BY c.relname), 'none')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('v','m')
   AND NOT COALESCE((SELECT o FROM unnest(c.reloptions) o WHERE o LIKE 'security_invoker=%')
                    = 'security_invoker=true', false);
"@
Probe "G11 no view in public runs as its owner" "none" $r.Out

# 6c. SECURITY DEFINER functions. Both are trigger functions owned by the superuser; the
# claim is that ob_app cannot call them to launder a write. Tested, not assumed.
$r = Sql -Role "postgres" -Password "test" -Query @"
SELECT COALESCE(string_agg(p.proname || ':' || pg_get_function_result(p.oid), ',' ORDER BY p.proname), 'none')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND p.prosecdef;
"@
Probe "G12 the only SECURITY DEFINER functions are the two triggers" `
      "queue_entity_extraction:trigger,queue_source_extraction:trigger" $r.Out

$r = Sql -Role "ob_app" -Password $appPw -Query "SELECT public.queue_entity_extraction();"
ProbeMatch "G13 ob_app cannot call a definer trigger function directly" `
           "trigger functions can only be called as triggers" $r.Out

# 6d. PUBLIC grants on tables/views/sequences.
$r = Sql -Role "postgres" -Password "test" -Query @"
SELECT COALESCE(string_agg(c.relname, ',' ORDER BY c.relname), 'none')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S')
   AND array_to_string(c.relacl, ' ') ~ '(^| )=';
"@
Probe "G14 no relation in public is granted to PUBLIC" "none" $r.Out

# 6e. Predefined roles - pg_read_all_data would undo everything above in one grant.
$r = Sql -Role "postgres" -Password "test" -Query @"
SELECT COALESCE(string_agg(r.rolname || '->' || g.rolname, ',' ORDER BY g.rolname), 'none')
  FROM pg_auth_members m
  JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles g ON g.oid = m.member
 WHERE r.rolname LIKE 'pg\_%' AND g.rolname IN ('ob_app','ob_app_memory','service_role','ob_plane_personal');
"@
Probe "G15 no app role holds a pg_* predefined role" "none" $r.Out

# 6f. Sequences: writable (the ops write above proves nextval), and not a read path.
$r = Sql -Role "ob_app" -Password $appPw -Query @"
SELECT count(*)::text FROM (
  SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public' AND c.relkind = 'S' OFFSET 0
) s WHERE NOT has_sequence_privilege('ob_app', s.oid, 'USAGE');
"@
Probe "G16 ob_app has USAGE on every public sequence (ops writes keep working)" "0" $r.Out

# ------------------------------------------------------------------------------------------
# 7. WHAT WOULD BREAK - measured, not predicted
# ------------------------------------------------------------------------------------------
Head "7. what a cutover would break, and whether it breaks loudly"

# The 15 extension/CRM tables are governed ONLY by `TO public USING (auth.uid() = user_id)`,
# and `auth.uid()` in this database is a stub returning NULL. The question this drill exists to
# answer is not "does the boundary hold" - it does - but "how does the cutover FAIL".
#
# MEASURED, and it is the worse of the two answers: `ob_app` has no USAGE on schema `auth`
# (probe G20 states that separately), yet the read does not error. It returns an EMPTY SET.
# openbrain-ext moved to a non-superuser role would report an empty CRM, not a broken one.
# That is why the promotion plan keeps openbrain-ext on `postgres` until the extension tables
# get a policy model that does not depend on a JWT this stack has never had.
$r = Sql -Role "postgres" -Password "test" -Query "SELECT count(*) FROM public.recipes WHERE name = 'H1-DRILL recipe';"
Probe "R4  RED: the recipe fixture IS there (superuser sees it)" "1" $r.Out

$r = Sql -Role "ob_app" -Password $appPw -Query "SELECT count(*) FROM public.recipes WHERE name = 'H1-DRILL recipe';"
Probe "G17 openbrain-ext's tables go SILENTLY EMPTY under ob_app - no error, no rows" "0" $r.Out

# Asked AS POSTGRES on purpose: asking as ob_app fails at PARSE time, because resolving the
# name `auth.uid()` needs USAGE on the schema before has_function_privilege() is ever called.
$r = Sql -Role "postgres" -Password "test" -Query "SELECT has_schema_privilege('ob_app','auth','USAGE')::text;"
Probe "G20 ob_app has NO usage on schema auth" "false" $r.Out

# The mechanism, stated as two measurements rather than as a belief about how RLS works:
# ob_app cannot call auth.uid() itself, but the policy that calls it on ob_app's behalf runs
# fine and yields NULL. The refusal is therefore invisible to the application.
$r = Sql -Role "ob_app" -Password $appPw -Query "SELECT auth.uid();"
ProbeMatch "G21 ...a DIRECT call to auth.uid() as ob_app is refused, loudly" `
           "permission denied for schema auth" $r.Out

$r = Sql -Role "postgres" -Password "test" -Query @"
SELECT count(DISTINCT tablename)::text || '|' || count(*)::text FROM pg_policies
 WHERE schemaname = 'public' AND roles::text = '{public}'
   AND (COALESCE(qual,'') LIKE '%auth.%' OR COALESCE(with_check,'') LIKE '%auth.%');
"@
Probe "G18 ...across 15 tables / 19 policies, so the blast radius is known" "15|19" $r.Out

# The graph/corpus tables the other eight clients use must still work as ob_app.
$r = Sql -Role "ob_app" -Password $appPw -Query @"
SELECT (SELECT count(*) FROM public.sources)      >= 0
   AND (SELECT count(*) FROM public.threads)      >= 0
   AND (SELECT count(*) FROM public.claims)       >= 0
   AND (SELECT count(*) FROM public.source_chunks)>= 0
   AND (SELECT count(*) FROM public.wiki_pages)   >= 0;
"@
Probe "G19 the corpus tables the other clients use are readable as ob_app" "t" $r.Out

# ------------------------------------------------------------------------------------------
# 8. Fixture teardown + verdict
# ------------------------------------------------------------------------------------------
Head "8. verdict"
Sql -Role "postgres" -Password "test" -Query @"
DELETE FROM public.recipes        WHERE name = 'H1-DRILL recipe';
DELETE FROM public.idea_revisions WHERE summary LIKE 'seeded%';
DELETE FROM public.agent_memories WHERE summary IN ('ops write');
DELETE FROM public.ideas          WHERE title LIKE 'H1-DRILL%idea';
DELETE FROM public.agent_memories WHERE workspace_id = 'H1-DRILL';
DELETE FROM public.thoughts       WHERE content LIKE 'H1-DRILL%';
"@ | Out-Null

Cleanup

if ($script:fail -eq 0) {
    Say ""
    Say "H1 DRILL PASSED - $($script:probeNo) probes, 0 failures."
    exit 0
} else {
    Say ""
    Say "H1 DRILL FAILED - $($script:fail) of $($script:probeNo) probes disagreed."
    exit 1
}
