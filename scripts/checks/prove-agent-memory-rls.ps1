# prove-agent-memory-rls.ps1 - the exposure boundary, proved IN THE DATABASE.
#
# WHAT THIS EXISTS FOR. dark-factory-unification PLAN.md amendment A2 (2026-08-30) moved the
# personal-plane boundary out of the readers and into Postgres. This script is the executable
# half of that claim. It does NOT enumerate readers - that method lost five rounds and A2
# retired it. It asserts ONE predicate, at the layer every reader passes through whether it
# was enumerated or not.
#
# ------------------------------------------------------------------------------------------
# EVERY GREEN HAS A RED BESIDE IT, AND THE REDS ARE WHOLE DATABASES
# ------------------------------------------------------------------------------------------
# The cheap version of this script would apply the migration and assert the personal row is
# gone. That passes just as well if the fixture never landed, if the query was wrong, or if
# the reader was broken for an unrelated reason. So it builds TWO throwaway databases from the
# SAME derived initdb chain:
#
#   RED   - the chain MINUS every boundary migration (180, 190, 195, 200). This is the
#           schema as it stood before this effort touched it.
#   GREEN - the full chain.
#
# THE RED USED TO BE "THE CHAIN MINUS init-agent-memory-rls.sql" AND THAT STOPPED BEING A RED.
# When the boundary was one file, removing it removed the boundary. It is now four - 180
# (policies + roles + views), 190 (the corpus predicate closes), 195 (DFU C.9 H3: exposure
# becomes a NOT NULL CHECKed COLUMN and the predicates read it) and 200 (the derived graph) -
# and 195 alone installs a working boundary on `thoughts` and `agent_memories`. A red that
# removed only 180 would have had 195's policies in it and would have reported "no leak" for
# the best possible reason and the worst possible one at the same time. The set is derived
# from ONE list below so it cannot drift file by file.
#
# The same fixtures are planted in both by the same SQL, and the same queries are run against
# both. A check only passes when RED LEAKS and GREEN DOES NOT. If a fixture fails to land, red
# fails and the run stops; "gone" cannot be reached by "never there".
#
# Four further reds are surgical rather than whole-database, because they isolate one line:
#   * the permissive-OR trap - put `USING (true)` back beside the narrow policy and the
#     boundary evaporates. This is why the migration DROPS the old policy instead of adding
#     to it, and four rounds of this effort died on exactly this reasoning error one layer up.
#   * FORCE ROW LEVEL SECURITY - with it the table OWNER is bound, without it the owner reads
#     everything.
#   * security_invoker on the PostgREST views - PostgREST's own advice is to expose views, and
#     a view without security_invoker reads the base table AS ITS OWNER. Following the advice
#     carelessly creates the bypass the migration exists to close.
#   * SET LOCAL vs SET - the tenancy variable must not survive its transaction, or a pooled
#     connection hands one request's identity to the next.
#   * THE PERSONAL POLICY'S ROLE - `agent_memories_personal_plane` must be granted TO
#     ob_plane_personal and NOT to service_role. Granted to service_role it still "works",
#     because ob_plane_personal is a member of service_role - and it also lets ANY
#     service_role session read a personal row by naming a tenant, since `ob.user_id` is an
#     ordinary GUC any role may set. This section exists because the boundary had drifted
#     exactly that way and nothing went red; see documentation/notes/u8h3-findings.md.
#
# AND THE WRITE CONTRACT (DFU C.9 H3, operator 2026-08-31), which is a different property
# from any of the above: the DATABASE must REFUSE a write whose exposure is ABSENT and one
# whose exposure is MALFORMED. Section 3b attempts both, against both tables, as the
# superuser - because NOT NULL and CHECK bind a superuser where RLS does not, and a check
# that only proved it for a non-superuser would be proving the weaker half.
#
# ------------------------------------------------------------------------------------------
# NO REAL PERSONAL DATA, EVER. Class 4 of the decision ladder.
# ------------------------------------------------------------------------------------------
# Every fixture is a synthetic string carrying this run's id, planted in a throwaway container
# that is destroyed at the end. The live plane is touched by exactly one READ-ONLY query, in
# the last section, which counts personal rows and expects 0.

[CmdletBinding()]
param(
    [switch]$KeepContainers,
    [switch]$SkipLive
)

# "Continue", not "Stop": PowerShell 5.1 turns a native command's stderr into ErrorRecords,
# so `docker rm -f <missing>` - which the initdb helper does on every start - would abort the
# run under "Stop". Fatal conditions below throw explicitly instead.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
. (Join-Path $PSScriptRoot "lib\ob-initdb.ps1")

$RunId   = (Get-Random -Maximum 999999).ToString("000000")
$MARKER  = "SYNTH-U5RLS-$RunId"
$PERSON  = "$MARKER-PERSONAL-PAYLOAD"
$OPSTEXT = "$MARKER-OPS-PAYLOAD"
$UID_ME  = "u5rls-user-$RunId"
$UID_OTHER = "u5rls-other-$RunId"

$RedDb   = "u5rls-red-$RunId"
$GreenDb = "u5rls-green-$RunId"
$RedRest = "u5rls-red-rest-$RunId"
$GrnRest = "u5rls-green-rest-$RunId"
$Net     = "u5rls-net-$RunId"

$script:Pass = 0
$script:Fail = 0
function Section([string]$t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Pass([string]$t)    { $script:Pass++; Write-Host "  PASS  $t" -ForegroundColor Green }
function Fail([string]$t)    { $script:Fail++; Write-Host "  FAIL  $t" -ForegroundColor Red }
function Note([string]$t)    { Write-Host "        $t" -ForegroundColor DarkGray }

# psql, as the SUPERUSER, for planting and for reading ground truth. Everything that is being
# PROVED runs through Qrole below instead.
function Q {
    param([string]$Db, [string]$Sql)
    $out = $Sql | docker exec -i $Db psql -U postgres -d openbrain -tA -v ON_ERROR_STOP=1 2>&1
    # psql echoes a command tag for every non-SELECT statement even in tuples-only mode, and
    # this script wraps almost every proved query in BEGIN/SET ROLE/SET LOCAL/COMMIT. Those
    # tags are dropped so a scalar query yields a scalar; ERROR lines are NOT dropped, because
    # a refusal is a result this script asserts on.
    $lines = @($out | ForEach-Object { $_.ToString().Trim() } |
               Where-Object { $_ -ne "" -and $_ -notmatch '^(SET|BEGIN|COMMIT|ROLLBACK|RESET)$' })
    return ($lines -join "`n").Trim()
}

# The same, but the statement runs after SET ROLE - i.e. as an access class, not as the
# superuser. This is the whole point: `postgres` is a superuser and bypasses RLS entirely, so
# a check that forgets to switch role proves nothing at all.
function Qrole {
    param([string]$Db, [string]$Role, [string]$Sql, [string]$UserId = "")
    $prefix = "SET ROLE $Role;`n"
    if ($UserId) { $prefix = "BEGIN;`nSET ROLE $Role;`nSET LOCAL ob.user_id = '$UserId';`n" }
    # The trailing semicolon is not cosmetic: without it COMMIT parses as a continuation of
    # the caller's SELECT and every tenancy check dies on a syntax error that looks like a
    # denial.
    $suffix = if ($UserId) { ";`nCOMMIT;" } else { "" }
    return (Q -Db $Db -Sql ($prefix + $Sql + $suffix))
}

function Cleanup {
    if ($KeepContainers) { Note "containers kept: $RedDb $GreenDb $RedRest $GrnRest ($Net)"; return }
    docker rm -f $RedRest $GrnRest $RedDb $GreenDb 2>$null | Out-Null
    docker network rm $Net 2>$null | Out-Null
}

# ------------------------------------------------------------------------------------------
# 0. The tree under test
# ------------------------------------------------------------------------------------------
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $Repo
try {

Section "the tree under test - the RECORDED GITLINK, not a working copy that never merges"
# A previous round of this effort was green about a tree that did not merge: the fix lived in
# an OB1 commit that was never `git add OB1`'d. The chain this script stages comes out of
# OB1/docker, so if the submodule working copy is dirty or on a different commit than the
# parent records, the run is testing something a merge would not ship.
$recorded = (git ls-tree HEAD OB1) -replace '^\S+\s+\S+\s+(\S+)\s.*$', '$1'
$actual   = (git -C OB1 rev-parse HEAD).Trim()
$dirty    = @(git -C OB1 status --porcelain)
if ($recorded -eq $actual -and $dirty.Count -eq 0) {
    Pass "OB1 working copy is clean and equals the recorded gitlink ($($actual.Substring(0,8)))"
} else {
    Fail "OB1 is dirty or diverged from the gitlink (recorded=$recorded actual=$actual dirty=$($dirty.Count)) - this run would not prove what merges"
    throw "gitlink mismatch"
}

# ------------------------------------------------------------------------------------------
# 1. Two databases from ONE derived chain
# ------------------------------------------------------------------------------------------
Section "two throwaway databases from the SAME derived initdb chain, one without the migration"
$compose = Join-Path $Repo "OB1\docker\docker-compose.yml"
$chain = Get-ObInitChain -ComposePath $compose
if ($chain.Count -lt 1) { Fail "could not derive the initdb chain from compose"; throw "no chain" }
# THE BOUNDARY, as ONE list. Every file here is removed to make the RED, and every one is
# asserted to be mounted, so a migration that quietly stopped being in compose fails this run
# instead of silently shrinking the red.
$BOUNDARY = @(
    "init-agent-memory-rls.sql",              # 180 - policies, roles, FORCE, views
    "init-agent-memory-corpus-failclosed.sql",# 190 - the corpus predicate stops defaulting open
    "init-agent-memory-exposure-column.sql",  # 195 - DFU C.9 H3: exposure becomes a column
    "init-graph-plane-rls.sql"                # 200 - the derived graph
)
$missingMig = @($BOUNDARY | Where-Object { $f = $_; -not ($chain | Where-Object { $_[0] -eq $f }) })
if ($missingMig.Count -gt 0) {
    Fail "boundary migration(s) not in the initdb chain - a fresh volume would never get them: $($missingMig -join ', ')"
    throw "migration not mounted"
}
Pass "the chain is derived from compose ($($chain.Count) migrations) and includes all $($BOUNDARY.Count) boundary files: $($BOUNDARY -join ', ')"

$greenDir = Join-Path $env:TEMP "u5rls-green-$RunId"
$redDir   = Join-Path $env:TEMP "u5rls-red-$RunId"
$nGreen = Copy-ObInitChain -Chain $chain -SourceDir (Join-Path $Repo "OB1\docker") -TargetDir $greenDir
$redChain = @($chain | Where-Object { $BOUNDARY -notcontains $_[0] })
$nRed   = Copy-ObInitChain -Chain $redChain -SourceDir (Join-Path $Repo "OB1\docker") -TargetDir $redDir
if ($nGreen -eq $chain.Count -and $nRed -eq $chain.Count - $BOUNDARY.Count) {
    Pass "staged GREEN ($nGreen migrations) and RED ($nRed - the chain MINUS all $($BOUNDARY.Count) boundary files)"
} else {
    Fail "staging mismatch: green $nGreen/$($chain.Count), red $nRed/$($chain.Count - $BOUNDARY.Count) - a mount names a missing file"
    throw "staging"
}

docker network create $Net | Out-Null
foreach ($pair in @(@($GreenDb, $greenDir), @($RedDb, $redDir))) {
    if (-not (Start-ObInitdb -Name $pair[0] -InitDir $pair[1] -DockerArgs @("--network", $Net))) {
        Fail "initdb did not complete for $($pair[0]) - nothing below is trustworthy"; throw "initdb"
    }
    $errs = Get-ObInitdbErrors -Name $pair[0]
    if ($errs) { Write-Host ($errs -join "`n") -ForegroundColor Red; Fail "init chain errors in $($pair[0])"; throw "initdb errors" }
}
Pass "both databases came up on the real schema with no init errors"

# The starting state is ESTABLISHED, not assumed. Every assertion below is a count or an
# absence and both are meaningless on a database whose starting state was never measured.
foreach ($db in @($GreenDb, $RedDb)) {
    $pre = Q $db "SELECT (SELECT count(*) FROM agent_memories) || '/' || (SELECT count(*) FROM thoughts)"
    if ($pre -eq "0/0") { Pass "$db is EMPTY before anything is planted (memories/thoughts = $pre)" }
    else { Fail "$db is not fresh (memories/thoughts = $pre)"; throw "stale db" }
}

# The migration landed in GREEN and did NOT land in RED - asserted, because "the red database
# is the old schema" is the assumption the whole comparison rests on.
$gForce = Q $GreenDb "SELECT relrowsecurity::text || relforcerowsecurity::text FROM pg_class WHERE relname='thoughts'"
$rForce = Q $RedDb   "SELECT relrowsecurity::text || relforcerowsecurity::text FROM pg_class WHERE relname='thoughts'"
if ($gForce -eq "truetrue") { Pass "GREEN: thoughts has RLS ENABLED and FORCED (relrowsecurity/relforce = $gForce)" }
else { Fail "GREEN: thoughts RLS state is '$gForce', expected 'truetrue'" }
if ($rForce -eq "falsefalse") { Pass "RED: thoughts has RLS OFF - exactly production's state today (= $rForce)" }
else { Fail "RED: thoughts RLS state is '$rForce', expected 'falsefalse'" }

$gPol = Q $GreenDb "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid WHERE c.relname='agent_memories' AND pg_get_expr(p.polqual,p.polrelid)='true'"
if ($gPol -eq "0") { Pass "GREEN: no USING (true) policy survives on agent_memories" }
else { Fail "GREEN: $gPol policy/policies on agent_memories still say USING (true) - the boundary is decorative" }
$rPol = Q $RedDb "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid WHERE c.relname='agent_memories' AND pg_get_expr(p.polqual,p.polrelid)='true'"
if ($rPol -eq "1") { Pass "RED: agent_memories still carries the USING (true) policy - the state A2 measured" }
else { Fail "RED: expected exactly one USING (true) policy on agent_memories, found $rPol" }

# ------------------------------------------------------------------------------------------
# 2. The synthetic fixture - identical in both databases
# ------------------------------------------------------------------------------------------
Section "a SYNTHETIC personal fixture, and an ops control beside it"
# The ops control exists so that "the personal row is absent" cannot pass because the reader
# returned nothing at all. Every absence assertion below is paired with the presence of the
# control in the same result set.
# ONE fixture text for both databases, and it ASKS THE SCHEMA which columns exist rather
# than being written twice. RED has no `exposure` column (195 is not in it) and GREEN's is
# NOT NULL, so a single static INSERT cannot work in both - and two hand-written fixtures
# would let the comparison drift into "the two databases got different rows".
#
# THE LEGACY UNLABELLED ROW IS PLANTED ONLY IN RED, and that is the point rather than a
# concession: after H3 an unlabelled thought is not a row the database will accept. Section
# 3b proves that directly, and section 3 asserts here that RED HAS one so the ops-visibility
# comparison still has its subject.
$fixture = @"
DO `$fx`$
DECLARE has_col BOOLEAN := EXISTS (
  SELECT 1 FROM information_schema.columns
   WHERE table_schema='public' AND table_name='thoughts' AND column_name='exposure');
BEGIN
  IF has_col THEN
    INSERT INTO thoughts (id, content, metadata, exposure) VALUES
      (900001, '$OPSTEXT', '{"exposure":"ops"}'::jsonb, 'ops'),
      (900002, '$PERSON',  '{"exposure":"personal"}'::jsonb, 'personal');
    INSERT INTO agent_memories (id, thought_id, workspace_id, memory_type, summary, content, metadata, exposure) VALUES
      ('11111111-1111-1111-1111-111111111111', 900001, 'ws-$RunId', 'decision',
       '$MARKER ops summary', '$OPSTEXT', '{"exposure":"ops"}'::jsonb, 'ops'),
      ('22222222-2222-2222-2222-222222222222', 900002, 'ws-$RunId', 'decision',
       '$MARKER personal summary', '$PERSON', '{"exposure":"personal"}'::jsonb, 'personal');
  ELSE
    INSERT INTO thoughts (id, content, metadata) VALUES
      (900001, '$OPSTEXT', '{"exposure":"ops"}'::jsonb),
      (900002, '$PERSON',  '{"exposure":"personal"}'::jsonb),
      (900003, '$MARKER-LEGACY-UNLABELLED', '{}'::jsonb);
    INSERT INTO agent_memories (id, thought_id, workspace_id, memory_type, summary, content, metadata) VALUES
      ('11111111-1111-1111-1111-111111111111', 900001, 'ws-$RunId', 'decision',
       '$MARKER ops summary', '$OPSTEXT', '{"exposure":"ops"}'::jsonb),
      ('22222222-2222-2222-2222-222222222222', 900002, 'ws-$RunId', 'decision',
       '$MARKER personal summary', '$PERSON', '{"exposure":"personal"}'::jsonb);
  END IF;
END
`$fx`$;
INSERT INTO agent_memory_review_actions (memory_id, action, notes, before, after) VALUES
  ('22222222-2222-2222-2222-222222222222', 'confirm', '$MARKER review note',
   jsonb_build_object('content','$PERSON'), jsonb_build_object('content','$PERSON'));
INSERT INTO agent_memory_recall_traces (id, workspace_id, query, schema_version, request_payload) VALUES
  ('33333333-3333-3333-3333-333333333333', 'ws-$RunId', '$PERSON as a query', 'v1',
   '{"enforced_exposure":["personal"]}'::jsonb);
INSERT INTO agent_memory_recall_items (trace_id, memory_id, rank, use_policy_snapshot) VALUES
  ('33333333-3333-3333-3333-333333333333', '22222222-2222-2222-2222-222222222222', 1,
   jsonb_build_object('content','$PERSON'));
SELECT 'planted';
"@
foreach ($db in @($GreenDb, $RedDb)) {
    $r = Q $db $fixture
    if ($r -match "planted") { Pass "$db : fixture planted (1 ops + 1 personal, with sidecars)" }
    else { Fail "$db : fixture failed - $r"; throw "fixture" }
}
# The asymmetry is ASSERTED rather than left implicit: RED must hold the legacy unlabelled
# row (it is the subject of the ops-visibility comparison below) and GREEN must not be able
# to hold one at all.
$redLegacy = Q $RedDb "SELECT count(*) FROM thoughts WHERE content = '$MARKER-LEGACY-UNLABELLED'"
if ($redLegacy -eq "1") { Pass "RED holds the legacy UNLABELLED corpus row - production's pre-boundary shape" }
else { Fail "RED does not hold the legacy unlabelled row (count=$redLegacy) - the comparison below has no subject" }
$greenCols = Q $GreenDb "SELECT is_nullable || '/' || COALESCE(column_default,'none') FROM information_schema.columns WHERE table_schema='public' AND table_name='thoughts' AND column_name='exposure'"
if ($greenCols -eq "NO/none") { Pass "GREEN: thoughts.exposure is NOT NULL with NO DEFAULT ($greenCols) - a writer must state the plane" }
else { Fail "GREEN: thoughts.exposure is '$greenCols', expected 'NO/none' - a DEFAULT would make the NOT NULL unreachable" }
# The tenancy column only exists in GREEN; the fixture above is deliberately identical in both
# so the comparison is not confounded by a different INSERT.
$null = Q $GreenDb "UPDATE agent_memories SET user_id='$UID_ME' WHERE id='22222222-2222-2222-2222-222222222222'; UPDATE thoughts SET user_id='$UID_ME' WHERE id=900002;"
Pass "GREEN: the personal fixture is stamped with a tenant ($UID_ME)"

# ------------------------------------------------------------------------------------------
# 3. THE PREDICATE - as the agent-plane role, on every content home
# ------------------------------------------------------------------------------------------
Section "the agent plane (service_role) reads agent_memories, thoughts and the sidecars"
# service_role is the agent/general access class: it is the role PostgREST switches into for
# every anonymous request, so it is the role every PostgREST-mediated reader in this stack
# already runs as - the scheduled wiki compiler included.
$targets = @(
    @{ n = "agent_memories";              q = "SELECT count(*) FROM agent_memories WHERE content LIKE '%$PERSON%'" },
    @{ n = "thoughts";                    q = "SELECT count(*) FROM thoughts WHERE content LIKE '%$PERSON%'" },
    @{ n = "agent_memory_review_actions"; q = "SELECT count(*) FROM agent_memory_review_actions WHERE before::text LIKE '%$PERSON%'" },
    @{ n = "agent_memory_recall_items";   q = "SELECT count(*) FROM agent_memory_recall_items WHERE use_policy_snapshot::text LIKE '%$PERSON%'" },
    @{ n = "agent_memory_recall_traces";  q = "SELECT count(*) FROM agent_memory_recall_traces WHERE query LIKE '%$PERSON%'" }
)
foreach ($t in $targets) {
    $red   = Qrole -Db $RedDb   -Role "service_role" -Sql $t.q
    $green = Qrole -Db $GreenDb -Role "service_role" -Sql $t.q
    if ($red -eq "1") { Pass "RED  $($t.n): the personal row IS readable by the agent plane (count=$red) - the leak is real" }
    else { Fail "RED  $($t.n): expected the leak (count=1), got '$red' - the green below would prove nothing" }
    if ($green -eq "0") { Pass "GREEN $($t.n): the personal row is INVISIBLE to the agent plane (count=$green)" }
    else { Fail "GREEN $($t.n): the personal row is still readable (count=$green)" }
}

Section "the ops rows remain fully readable - the running system is not broken"
$opsChecks = @(
    @{ n = "agent_memories ops row";  q = "SELECT count(*) FROM agent_memories WHERE content LIKE '%$OPSTEXT%'" },
    @{ n = "thoughts ops row";        q = "SELECT count(*) FROM thoughts WHERE content LIKE '%$OPSTEXT%'" }
)
foreach ($t in $opsChecks) {
    $green = Qrole -Db $GreenDb -Role "service_role" -Sql $t.q
    if ($green -eq "1") { Pass "GREEN $($t.n): still readable (count=$green)" }
    else { Fail "GREEN $($t.n): count=$green - the migration broke a reader it was not supposed to touch" }
}
# THE LEGACY UNLABELLED ROW: THIS ASSERTION INVERTED, AND SAYING SO IS THE POINT.
#
# It used to read "unlabelled corpus stays visible ON PURPOSE - production holds 12,989 such
# rows", because the corpus predicate was `exposure IS NULL OR exposure = 'ops'` and 12,989
# of 12,993 rows carried no label. That was TRUE THEN and it is not the design now, and it
# did not change by being dropped:
#   * 190 (C.8 clause 3, operator 2026-08-30) LABELLED all ~13,000 of them 'ops' FIRST, then
#     removed the `IS NULL` arm. No row changed visibility; the fail-open arm went away.
#   * 195 (C.9 H3, operator 2026-08-31) carried those same labels into a NOT NULL CHECKed
#     COLUMN, so "unlabelled" stopped being a state a row can be in.
# The visibility of the real corpus is unchanged across both - 13,001 ops rows before and
# after, measured on the live database. What changed is that the unlabelled row is no longer
# READ as ops; it is REFUSED at the write, which section 3b proves directly.
$redLegacyVisible = Qrole -Db $RedDb -Role "service_role" -Sql "SELECT count(*) FROM thoughts WHERE content LIKE '%$MARKER-LEGACY-UNLABELLED%'"
if ($redLegacyVisible -eq "1") { Pass "RED: the unlabelled corpus row is readable by the agent plane (count=$redLegacyVisible) - the pre-boundary behaviour" }
else { Fail "RED: the unlabelled row was not readable (count=$redLegacyVisible) - RED is not the pre-boundary schema" }
Note "in GREEN there is no such row to read: an unlabelled write is REFUSED (section 3b)"

# ------------------------------------------------------------------------------------------
# 3b. THE WRITE CONTRACT - the DATABASE refuses an ABSENT and a MALFORMED exposure
# ------------------------------------------------------------------------------------------
# DFU C.9 H3's own validation clause, and it is a DIFFERENT property from everything above.
# Sections 3-7 are about what a caller may READ. This is about what the database will ACCEPT,
# and it is enforced by NOT NULL and a CHECK rather than by a policy - which matters, because
# a constraint binds a SUPERUSER and a policy does not. So these attempts are made AS
# postgres: the strongest caller in the system, the one every openbrain-* container connects
# as, and the one an application-layer guard would not stop.
#
# EVERY REFUSAL HAS A LIVE POSITIVE CONTROL beside it in the same statement shape. A refusal
# that cannot be told apart from "the INSERT was malformed for some other reason" proves
# nothing, so each pair differs in exactly the exposure value and nothing else.
Section "the WRITE contract - an absent or malformed exposure is refused BY THE DATABASE"

$h3 = @(
    @{ n = "thoughts, exposure ABSENT";
       q = "INSERT INTO thoughts (content, metadata) VALUES ('$MARKER-H3-ABSENT', jsonb_build_object('exposure','ops'));";
       want = "null value in column"
       why  = "the jsonb mirror said ops and the row was still refused - a mirror is not a constraint" },
    @{ n = "thoughts, exposure MALFORMED";
       q = "INSERT INTO thoughts (content, exposure) VALUES ('$MARKER-H3-BAD', 'opsy');";
       want = "violates check constraint"
       why  = "a misspelling is refused rather than resolved at read time" },
    @{ n = "thoughts, exposure WRONG CASE";
       q = "INSERT INTO thoughts (content, exposure) VALUES ('$MARKER-H3-CASE', 'OPS');";
       want = "violates check constraint"
       why  = "the CHECK is case-sensitive, so 'OPS' is not 'ops'" },
    @{ n = "agent_memories, exposure ABSENT";
       q = "INSERT INTO agent_memories (workspace_id, memory_type, summary, content, metadata) VALUES ('ws-$RunId','decision','$MARKER-H3-ABSENT','$MARKER-H3-ABSENT',jsonb_build_object('exposure','ops'));";
       want = "null value in column"
       why  = "the same rule on the second table - two tables are two places a rule can be missing" },
    @{ n = "agent_memories, exposure MALFORMED";
       q = "INSERT INTO agent_memories (workspace_id, memory_type, summary, content, exposure) VALUES ('ws-$RunId','decision','$MARKER-H3-BAD','$MARKER-H3-BAD','personel');";
       want = "violates check constraint"
       why  = "a plausible typo, refused" }
)
foreach ($t in $h3) {
    $out = Q $GreenDb ("BEGIN; " + $t.q + " ROLLBACK;")
    if ($out -match [regex]::Escape($t.want)) {
        Pass "GREEN $($t.n): REFUSED by the database ($($t.want)) - $($t.why)"
    } else {
        Fail "GREEN $($t.n): NOT refused. Expected '$($t.want)', got: $out"
    }
}

# THE POSITIVE CONTROLS. Same statements, legal exposure values, and they must SUCCEED - or
# every refusal above is explained by the INSERT being broken for an unrelated reason.
$h3ok = @(
    @{ n = "thoughts, exposure='ops'";
       q = "INSERT INTO thoughts (content, exposure) VALUES ('$MARKER-H3-OK-OPS', 'ops');" },
    @{ n = "thoughts, exposure='personal'";
       q = "INSERT INTO thoughts (content, exposure) VALUES ('$MARKER-H3-OK-PERS', 'personal');" },
    @{ n = "agent_memories, exposure='ops'";
       q = "INSERT INTO agent_memories (workspace_id, memory_type, summary, content, exposure) VALUES ('ws-$RunId','decision','$MARKER-H3-OK','$MARKER-H3-OK','ops');" }
)
foreach ($t in $h3ok) {
    $out = Q $GreenDb ("BEGIN; " + $t.q + " SELECT 'accepted'; ROLLBACK;")
    if ($out -match "accepted") { Pass "GREEN CONTROL $($t.n): accepted - the refusals above are about the VALUE, not the statement" }
    else { Fail "GREEN CONTROL $($t.n): a legal write was refused, so this section proves nothing: $out" }
}

# AND THE RED, because the whole point is that this is NEW. The same absent write against the
# pre-boundary schema must SUCCEED and leave a readable row.
$redAbsent = Q $RedDb ("BEGIN; INSERT INTO thoughts (content, metadata) VALUES ('$MARKER-H3-ABSENT', '{}'::jsonb); SELECT 'accepted'; ROLLBACK;")
if ($redAbsent -match "accepted") { Pass "RED: the same unlabelled write is ACCEPTED on the pre-boundary schema - H3 is a real change" }
else { Fail "RED: the unlabelled write was already refused, so the green above proves nothing: $redAbsent" }

# NOTHING PERSISTED. Every attempt above ran inside BEGIN/ROLLBACK; asserted rather than
# assumed, because a fixture that outlived its section is how a later count goes wrong.
$leftovers = Q $GreenDb "SELECT (SELECT count(*) FROM thoughts WHERE content LIKE '$MARKER-H3-%') + (SELECT count(*) FROM agent_memories WHERE summary LIKE '$MARKER-H3-%')"
if ($leftovers -eq "0") { Pass "no write-contract probe persisted (count=$leftovers)" }
else { Fail "$leftovers write-contract probe row(s) survived their ROLLBACK" }

# ------------------------------------------------------------------------------------------
# 4. THROUGH POSTGREST, configured exactly as compose configures it
# ------------------------------------------------------------------------------------------
Section "through PostgREST - anon role = service_role, the production configuration untouched"
foreach ($p in @(@($RedRest, $RedDb), @($GrnRest, $GreenDb))) {
    docker run -d --name $p[0] --network $Net `
        -e "PGRST_DB_URI=postgres://postgres:test@$($p[1]):5432/openbrain" `
        -e "PGRST_DB_SCHEMAS=public" `
        -e "PGRST_DB_ANON_ROLE=service_role" `
        -e "PGRST_SERVER_PORT=3000" `
        postgrest/postgrest:v12.2.3 | Out-Null
}
function Rest {
    param([string]$Name, [string]$Path)
    for ($i = 0; $i -lt 45; $i++) {
        $r = docker run --rm --network $Net curlimages/curl:8.10.1 -s -m 5 "http://${Name}:3000$Path" 2>&1
        if ($LASTEXITCODE -eq 0 -and $r -and $r -notmatch "Connection refused") { return ($r -join "") }
        Start-Sleep 2
    }
    return ""
}
$restRed   = Rest $RedRest "/thoughts?select=content"
$restGreen = Rest $GrnRest "/thoughts?select=content"
if ($restRed -match [regex]::Escape($PERSON)) { Pass "RED  PostgREST /thoughts hands back the personal payload verbatim" }
else { Fail "RED  PostgREST did not return the personal payload - the green below proves nothing (body: $($restRed.Substring(0,[Math]::Min(120,$restRed.Length))))" }
if ($restGreen -and $restGreen -notmatch [regex]::Escape($PERSON)) { Pass "GREEN PostgREST /thoughts does NOT contain the personal payload" }
else { Fail "GREEN PostgREST /thoughts still contains the personal payload" }
if ($restGreen -match [regex]::Escape($OPSTEXT)) { Pass "GREEN PostgREST /thoughts still returns the ops control - it filtered, it did not fail" }
else { Fail "GREEN PostgREST returned no ops row either - this is breakage, not a boundary" }

$restGreenMem = Rest $GrnRest "/agent_memories?select=content"
if ($restGreenMem -notmatch [regex]::Escape($PERSON) -and $restGreenMem -match [regex]::Escape($OPSTEXT)) {
    Pass "GREEN PostgREST /agent_memories: personal absent, ops present"
} else { Fail "GREEN PostgREST /agent_memories: unexpected body $($restGreenMem.Substring(0,[Math]::Min(160,$restGreenMem.Length)))" }

$restGreenView = Rest $GrnRest "/v_agent_memories?select=content"
if ($restGreenView -notmatch [regex]::Escape($PERSON) -and $restGreenView -match [regex]::Escape($OPSTEXT)) {
    Pass "GREEN PostgREST /v_agent_memories (the security_invoker view): personal absent, ops present"
} else { Fail "GREEN PostgREST /v_agent_memories: unexpected body $($restGreenView.Substring(0,[Math]::Min(160,$restGreenView.Length)))" }

# ------------------------------------------------------------------------------------------
# 5. THE BOUNDARY DISCRIMINATES - it does not merely deny
# ------------------------------------------------------------------------------------------
Section "an appropriately-scoped human context DOES see the row - and only its own tenant's"
# A boundary that returns nothing to everybody is not a boundary, it is an outage. Axis 1
# (tenancy = column + session variable) is what makes the difference, and axis 2 (access class
# = role) is what decides who may use it.
$seen = Qrole -Db $GreenDb -Role "ob_plane_personal" -UserId $UID_ME `
        -Sql "SELECT count(*) FROM agent_memories WHERE content LIKE '%$PERSON%'"
if ($seen -eq "1") { Pass "the personal plane WITH the right tenant sees the row (count=$seen)" }
else { Fail "the personal plane cannot see its own row (count=$seen) - the boundary denies rather than discriminates" }

$seenT = Qrole -Db $GreenDb -Role "ob_plane_personal" -UserId $UID_ME `
         -Sql "SELECT count(*) FROM thoughts WHERE content LIKE '%$PERSON%'"
if ($seenT -eq "1") { Pass "the same holds on thoughts (count=$seenT)" }
else { Fail "the personal plane cannot see its own mirrored thought (count=$seenT)" }

$other = Qrole -Db $GreenDb -Role "ob_plane_personal" -UserId $UID_OTHER `
         -Sql "SELECT count(*) FROM agent_memories WHERE content LIKE '%$PERSON%'"
if ($other -eq "0") { Pass "the personal plane with a DIFFERENT tenant sees nothing (count=$other) - tenancy is load-bearing, not decorative" }
else { Fail "another tenant on the personal plane read the row (count=$other) - axis 1 does nothing" }

$noTenant = Qrole -Db $GreenDb -Role "ob_plane_personal" `
            -Sql "SELECT count(*) FROM agent_memories WHERE content LIKE '%$PERSON%'"
if ($noTenant -eq "0") { Pass "the personal plane with NO tenant set sees nothing (count=$noTenant) - unset fails closed" }
else { Fail "an unset tenant read the personal row (count=$noTenant) - the default is fail-open" }

# THE ROLE THE PERSONAL POLICY IS GRANTED TO, WHICH IS NOT A DETAIL.
#
# `ob_plane_personal` is a MEMBER of `service_role`, so a policy granted TO service_role
# still lets the personal plane in and every check above still passes - while ALSO letting
# ANY service_role session in. `ob.user_id` is an ordinary GUC with no privilege attached to
# setting it, so under that arrangement the only thing between the unauthenticated PostgREST
# role and a personal memory is knowing a tenant id. The boundary had drifted exactly that
# way between two rounds of init-graph-plane-rls.sql and nothing went red, because every
# assertion anyone had written was about a caller who was ALLOWED to see the row.
#
# So this asserts the negative: the ops-plane role, doing everything the personal role does,
# must still see nothing. The positive control is the line above it - the same query, the
# same tenant, the personal role, one row.
$opsWithTenant = Qrole -Db $GreenDb -Role "service_role" -UserId $UID_ME `
                 -Sql "SELECT count(*) FROM agent_memories WHERE content LIKE '%$PERSON%'"
if ($opsWithTenant -eq "0") {
    Pass "service_role SETTING THE RIGHT TENANT still sees nothing (count=$opsWithTenant) - the personal policy is not granted to it"
} else {
    Fail "service_role read the personal memory by naming a tenant (count=$opsWithTenant) - agent_memories_personal_plane is granted TO service_role, and ob.user_id is a GUC anyone may set"
}
$opsWithTenantT = Qrole -Db $GreenDb -Role "service_role" -UserId $UID_ME `
                  -Sql "SELECT count(*) FROM thoughts WHERE content LIKE '%$PERSON%'"
if ($opsWithTenantT -eq "0") { Pass "the same holds on thoughts (count=$opsWithTenantT)" }
else { Fail "service_role read the personal thought by naming a tenant (count=$opsWithTenantT)" }

# And the role grant is read from the catalogue as well, because the behavioural check above
# only fires for the ONE table and the ONE tenant it names. This one is a property of every
# personal policy in the schema.
$wrongRole = Q $GreenDb @"
SELECT COALESCE(string_agg(p.tablename || '.' || p.policyname || ' -> ' || p.roles::text, ', '), 'none')
  FROM pg_policies p
 WHERE p.schemaname = 'public' AND p.policyname LIKE '%personal_plane%'
   AND NOT (p.roles::text[] @> ARRAY['ob_plane_personal']::text[]
            AND NOT (p.roles::text[] @> ARRAY['service_role']::text[]));
"@
if ($wrongRole -eq "none") { Pass "every *_personal_plane policy is granted TO ob_plane_personal and to nothing wider" }
else { Fail "personal-plane policy/policies granted to the wrong role: $wrongRole" }

Section "SET LOCAL, never plain SET - the property that stops a pooler leaking one request into the next"
# A2 names this explicitly. The check is that the tenancy variable does not survive its
# transaction: a connection returned to a pool must not carry the previous caller's identity.
$leak = Q $GreenDb @"
BEGIN;
SET ROLE ob_plane_personal;
SET LOCAL ob.user_id = '$UID_ME';
SELECT 'in-txn=' || count(*) FROM agent_memories WHERE content LIKE '%$PERSON%';
COMMIT;
SET ROLE ob_plane_personal;
SELECT 'after-txn=' || count(*) FROM agent_memories WHERE content LIKE '%$PERSON%';
"@
if ($leak -match "in-txn=1" -and $leak -match "after-txn=0") {
    Pass "SET LOCAL is scoped to its transaction (in-txn=1, after-txn=0) - the next request on this connection inherits nothing"
} else { Fail "SET LOCAL scoping is wrong: $leak" }

$sticky = Q $GreenDb @"
SET ROLE ob_plane_personal;
SET ob.user_id = '$UID_ME';
SELECT 'plain-set-then-later=' || count(*) FROM agent_memories WHERE content LIKE '%$PERSON%';
"@
if ($sticky -match "plain-set-then-later=1") {
    Pass "RED: a plain SET persists past the statement that needed it - which is precisely why the contract is SET LOCAL"
} else { Fail "RED: plain SET did not persist, so this check proves nothing about the difference: $sticky" }

# ------------------------------------------------------------------------------------------
# 6. FORCE ROW LEVEL SECURITY - the owner is bound too
# ------------------------------------------------------------------------------------------
Section "FORCE ROW LEVEL SECURITY - the table OWNER is bound, and without it the owner is not"
# READ THIS BEFORE BELIEVING THE PASS. In production these tables are owned by `postgres`,
# which is a SUPERUSER, and a superuser bypasses RLS whether FORCE is set or not. FORCE binds
# a NON-SUPERUSER owner. So this section transfers ownership to a non-superuser role INSIDE
# THE THROWAWAY DATABASE to demonstrate that the flag does what the migration claims - and the
# migration's header says in as many words that the flag is inert in production until
# ownership moves off the superuser. That is a finding, not a hidden assumption.
$null = Q $GreenDb "CREATE ROLE u5rls_owner NOLOGIN NOBYPASSRLS; GRANT service_role TO u5rls_owner; ALTER TABLE public.agent_memories OWNER TO u5rls_owner;"
$ownerForced = Qrole -Db $GreenDb -Role "u5rls_owner" -Sql "SELECT count(*) FROM agent_memories WHERE content LIKE '%$PERSON%'"
if ($ownerForced -eq "0") { Pass "GREEN: the OWNER is bound by its own policies (count=$ownerForced) - FORCE is doing the work" }
else { Fail "GREEN: the owner read the personal row (count=$ownerForced) despite FORCE" }
$null = Q $GreenDb "ALTER TABLE public.agent_memories NO FORCE ROW LEVEL SECURITY;"
$ownerUnforced = Qrole -Db $GreenDb -Role "u5rls_owner" -Sql "SELECT count(*) FROM agent_memories WHERE content LIKE '%$PERSON%'"
if ($ownerUnforced -eq "1") { Pass "RED: with FORCE cleared the same owner reads the personal row (count=$ownerUnforced) - the flag is load-bearing" }
else { Fail "RED: clearing FORCE changed nothing (count=$ownerUnforced) - this check proves nothing" }
$null = Q $GreenDb "ALTER TABLE public.agent_memories FORCE ROW LEVEL SECURITY; ALTER TABLE public.agent_memories OWNER TO postgres;"
$restored = Q $GreenDb "SELECT relforcerowsecurity::text || '/' || pg_get_userbyid(relowner) FROM pg_class WHERE relname='agent_memories'"
if ($restored -eq "true/postgres") { Pass "the throwaway is restored to the shipped state ($restored)" }
else { Fail "could not restore the throwaway's state: $restored" }

# ------------------------------------------------------------------------------------------
# 7. The two traps this design walks past
# ------------------------------------------------------------------------------------------
Section "TRAP 1 - permissive policies are OR'd, so a surviving USING (true) is the whole boundary"
# This is the reasoning error that cost four rounds, one layer up: adding a predicate BESIDE an
# unconditional allow changes nothing. It is why the migration DROPS the old policy.
$null = Q $GreenDb "CREATE POLICY u5rls_trap ON public.agent_memories FOR ALL TO service_role USING (true) WITH CHECK (true);"
$trapped = Qrole -Db $GreenDb -Role "service_role" -Sql "SELECT count(*) FROM agent_memories WHERE content LIKE '%$PERSON%'"
if ($trapped -eq "1") { Pass "RED: one re-added USING (true) policy restores the leak in full (count=$trapped)" }
else { Fail "RED: re-adding USING (true) did not restore the leak (count=$trapped) - this check proves nothing" }
$null = Q $GreenDb "DROP POLICY u5rls_trap ON public.agent_memories;"
$untrapped = Qrole -Db $GreenDb -Role "service_role" -Sql "SELECT count(*) FROM agent_memories WHERE content LIKE '%$PERSON%'"
if ($untrapped -eq "0") { Pass "GREEN: dropping it closes the boundary again (count=$untrapped)" }
else { Fail "GREEN: the leak survived removing the trap policy (count=$untrapped)" }

Section "TRAP 2 - PostgREST advises views, and a view without security_invoker is a NEW bypass"
# The migration ships v_agent_memories / v_thoughts WITH (security_invoker = true). Without
# that option a view reads its base table as ITS OWNER - here the superuser - and hands the
# personal row straight back. Following the advice carelessly opens the hole the migration is
# closing, so the difference is proved rather than commented.
$null = Q $GreenDb "CREATE VIEW public.u5rls_bad_v AS SELECT id, content FROM public.agent_memories; GRANT SELECT ON public.u5rls_bad_v TO service_role;"
$badView = Qrole -Db $GreenDb -Role "service_role" -Sql "SELECT count(*) FROM u5rls_bad_v WHERE content LIKE '%$PERSON%'"
if ($badView -eq "1") { Pass "RED: a view WITHOUT security_invoker leaks the personal row (count=$badView)" }
else { Fail "RED: the no-invoker view did not leak (count=$badView) - this check proves nothing" }
$goodView = Qrole -Db $GreenDb -Role "service_role" -Sql "SELECT count(*) FROM v_agent_memories WHERE content LIKE '%$PERSON%'"
if ($goodView -eq "0") { Pass "GREEN: the shipped v_agent_memories (security_invoker = true) does not (count=$goodView)" }
else { Fail "GREEN: the shipped view leaked the personal row (count=$goodView)" }
$viOption = Q $GreenDb "SELECT count(*) FROM pg_class WHERE relname IN ('v_agent_memories','v_thoughts') AND 'security_invoker=true' = ANY(reloptions)"
if ($viOption -eq "2") { Pass "both shipped views carry security_invoker=true in reloptions" }
else { Fail "only $viOption of 2 shipped views carry security_invoker=true" }
$null = Q $GreenDb "DROP VIEW public.u5rls_bad_v;"

Section "TRAP 3 - TRUNCATE is not RLS-filterable, so the read path must not hold it"
$trunc = Qrole -Db $GreenDb -Role "service_role" -Sql "BEGIN; TRUNCATE public.agent_memories; ROLLBACK;"
if ($trunc -match "permission denied") { Pass "GREEN: the agent plane cannot TRUNCATE agent_memories (permission denied)" }
else { Fail "GREEN: TRUNCATE was not refused - one statement still empties the table past every policy ($trunc)" }
$truncRed = Qrole -Db $RedDb -Role "service_role" -Sql "BEGIN; TRUNCATE public.agent_memories; ROLLBACK;"
if ($truncRed -notmatch "permission denied") { Pass "RED: the same role CAN truncate it today - the grant reduction is a real change" }
else { Fail "RED: TRUNCATE was already refused, so the grant reduction changed nothing ($truncRed)" }

Section "TRAP 4 - the WRITE side: an ops-plane connection cannot mint a personal memory"
# memory-plane PLAN 1.1: a record's maximum exposure equals the access plane of the context
# that wrote it. WITH CHECK states that as a constraint instead of a convention.
#
# THIS CHECK WAS WRITTEN AGAINST A SCHEMA THAT NO LONGER EXISTS, and the way it broke is
# worth keeping. It used to INSERT as service_role and read the RLS refusal. Since
# 200-init-graph-plane-rls.sql section 6a, service_role has NO INSERT on agent_memories at
# all - the write door was closed on a table nothing writes through it - so the same
# statement now fails with `permission denied` before any policy is consulted. The old
# assertion turned RED on a boundary that had got STRICTER, which is precisely the shape of
# a check that has stopped measuring its subject.
#
# So it is now two checks, and they are different claims:
#   (a) THE GRANT. service_role cannot write this table at all. That is the stronger
#       containment and it is what production runs.
#   (b) THE POLICY UNDERNEATH IT. If the grant were ever restored - a re-grant, a new door,
#       revert-graph-plane-rls.sql - WITH CHECK must still refuse a personal write. That is
#       tested by granting INSERT inside a transaction and rolling it back, so the property
#       is proved without the throwaway ever ending up in that state.
$grantState = Q $GreenDb "SELECT has_table_privilege('service_role','public.agent_memories','INSERT')::text"
if ($grantState -eq "false") {
    Pass "GREEN (a): service_role holds NO INSERT on agent_memories - the write door is closed at the grant (200 section 6a), which is stronger than a policy refusal"
} else {
    Fail "GREEN (a): service_role still holds INSERT on agent_memories - 200 section 6a did not apply"
}

$wrote = Q $GreenDb @"
BEGIN;
GRANT INSERT ON public.agent_memories TO service_role;
SET ROLE service_role;
INSERT INTO agent_memories (workspace_id, memory_type, summary, content, metadata, exposure)
VALUES ('ws-$RunId','decision','$MARKER forged','$MARKER forged personal','{"exposure":"personal"}'::jsonb,'personal');
ROLLBACK;
"@
$wroteErr = ($wrote -split "`n" | Where-Object { $_ -match "row-level security" } | Select-Object -First 1)
if ($wroteErr) { Pass "GREEN (b): with INSERT restored, the ops plane is STILL refused when it writes exposure=personal ($wroteErr)" }
else { Fail "GREEN (b): the ops plane minted a personal memory once the grant was restored - access does not bound writes ($wrote)" }

$wroteOps = Q $GreenDb @"
BEGIN;
GRANT INSERT ON public.agent_memories TO service_role;
SET ROLE service_role;
INSERT INTO agent_memories (workspace_id, memory_type, summary, content, metadata, exposure)
VALUES ('ws-$RunId','decision','$MARKER ok','$MARKER ordinary ops write','{"exposure":"ops"}'::jsonb,'ops');
SELECT 'ops-write-ok';
ROLLBACK;
"@
if ($wroteOps -match "ops-write-ok") { Pass "GREEN (b) CONTROL: the same statement with exposure=ops SUCCEEDS - the refusal above is about the plane, not the grant or the statement" }
else { Fail "GREEN (b) CONTROL: an ops write was refused, so the refusal above proves nothing ($wroteOps)" }

$stillClosed = Q $GreenDb "SELECT has_table_privilege('service_role','public.agent_memories','INSERT')::text"
if ($stillClosed -eq "false") { Pass "the temporary grant was rolled back - the throwaway is back to the shipped state" }
else { Fail "the temporary INSERT grant survived its ROLLBACK" }

# ------------------------------------------------------------------------------------------
# 8. The live plane - READ ONLY
# ------------------------------------------------------------------------------------------
Section "the live plane, read-only: production holds ZERO personal rows"
if ($SkipLive) { Note "skipped (-SkipLive)" }
else {
    # READ THE COLUMN WHERE IT EXISTS, and say which one was read. The live database may or
    # may not have 195 applied at any given moment, and a query that silently fell back to the
    # jsonb mirror would report on the non-authoritative copy without the output saying so.
    # THE COLUMN HALF IS EVALUATED ONLY WHERE THE COLUMN EXISTS, and the output SAYS which
    # half ran. The first version of this counted `to_jsonb(row)->>'exposure' IS DISTINCT
    # FROM 'ops'` unconditionally: on a database without the column that expression is NULL
    # for every row, so it counted the WHOLE TABLE as personal and reported the live plane
    # as dirty. A probe that reads a column that is not there does not measure nothing - it
    # measures everything, in the alarming direction.
    $live = (docker exec openbrain-db psql -U postgres -d openbrain -tA -c @"
SELECT CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                          WHERE table_schema='public' AND table_name='agent_memories' AND column_name='exposure')
            THEN 'source=column' ELSE 'source=jsonb-mirror-only' END;
SELECT 'personal_memories=' || count(*) FROM agent_memories WHERE COALESCE(metadata->>'exposure','personal') <> 'ops';
SELECT 'personal_thoughts=' || count(*) FROM thoughts WHERE metadata->>'exposure' IS NOT NULL AND metadata->>'exposure' <> 'ops';
SELECT 'personal_memories_col=' || CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
       WHERE table_schema='public' AND table_name='agent_memories' AND column_name='exposure')
  THEN (SELECT count(*)::text FROM agent_memories WHERE to_jsonb(agent_memories)->>'exposure' IS DISTINCT FROM 'ops')
  ELSE 'n/a' END;
SELECT 'personal_thoughts_col=' || CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
       WHERE table_schema='public' AND table_name='thoughts' AND column_name='exposure')
  THEN (SELECT count(*)::text FROM thoughts WHERE to_jsonb(thoughts)->>'exposure' IS DISTINCT FROM 'ops')
  ELSE 'n/a' END;
"@ 2>&1) -join " "
    $mirrorClean = ($live -match "personal_memories=0" -and $live -match "personal_thoughts=0")
    $colClean = ($live -match "personal_memories_col=(0|n/a)" -and $live -match "personal_thoughts_col=(0|n/a)")
    if ($mirrorClean -and $colClean) {
        if ($live -match "source=column") { Pass "production: 0 personal rows by the COLUMN and 0 by the mirror - they agree ($live)" }
        else { Pass "production: 0 personal rows by the mirror; the COLUMN is not applied to this volume yet, and this run says so rather than guessing ($live)" }
    } else { Fail "production is not clean, or the column and the mirror disagree: $live" }
    $liveState = (docker exec openbrain-db psql -U postgres -d openbrain -tA -c @"
SELECT relname || '=' || relrowsecurity::text || relforcerowsecurity::text FROM pg_class
 WHERE relname IN ('agent_memories','thoughts') ORDER BY relname;
"@ 2>&1) -join " "
    Note "production RLS state, read-only and unchanged by this run: $liveState"
    Note "whether the live volume RUNS this tree is the deploy gate, not this drill - see PROMOTION-RUNBOOK.md"
}

# ------------------------------------------------------------------------------------------
# 9. The fixture is destroyed
# ------------------------------------------------------------------------------------------
Section "the fixture is destroyed"
# Both databases are throwaway containers created by this run and removed by it, so the
# fixture cannot outlive the script. Asserted rather than assumed: a previous drill in this
# family silently reused a surviving container from an earlier run.
if (-not $KeepContainers) {
    Cleanup
    $left = @(docker ps -a --format "{{.Names}}" | Where-Object { $_ -match "u5rls-.*-$RunId" })
    if ($left.Count -eq 0) { Pass "every container this run created is gone ($RedDb, $GreenDb, both PostgRESTs, $Net)" }
    else { Fail "containers survived the run: $($left -join ', ')" }
    Remove-Item $greenDir, $redDir -Recurse -Force -ErrorAction SilentlyContinue
} else { Note "containers kept by request - the fixture is still on disk" }

} catch {
    Write-Host ("  aborted: " + $_.Exception.Message) -ForegroundColor Red
    $script:Fail++
} finally {
    # Cleanup belongs HERE and not only in section 9: a throw anywhere above would otherwise
    # leave two databases and two PostgRESTs running, and the next run's "the database is
    # EMPTY before anything is planted" would be measuring somebody else's leftovers. A drill
    # in this family has already been fooled exactly that way.
    Cleanup
    Pop-Location
}

Write-Host ""
if ($script:Fail -eq 0) {
    Write-Host "AGENT-MEMORY RLS PROOF PASSED - $($script:Pass) checks, every green with a red beside it" -ForegroundColor Green
    exit 0
} else {
    Write-Host "AGENT-MEMORY RLS PROOF FAILED - $($script:Pass) passed, $($script:Fail) failed" -ForegroundColor Red
    exit 1
}
