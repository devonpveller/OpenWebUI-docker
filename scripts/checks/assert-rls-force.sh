#!/bin/sh
# assert-rls-force.sh - THE EXPOSURE BOUNDARY ASSERTS ITSELF ON BOOT.
#
# dark-factory-unification PLAN.md C.9 H2. `init-*-rls.sql` runs only on a FRESH volume, so a
# restore, a rebuild, or a promotion where the runbook was skipped brings the database up
# FORCE-off and silently unprotected. Nothing in this stack looked at `relforcerowsecurity`.
# This script does, on every health probe, and it is wired so that a database which cannot
# prove the boundary is up DOES NOT GET USED: `openbrain-db`'s healthcheck runs it, and nine
# services declare `depends_on: openbrain-db: condition: service_healthy`.
#
# ------------------------------------------------------------------------------------------
# WHERE THIS FILE LIVES, AND WHERE IT BELONGS
# ------------------------------------------------------------------------------------------
# Its final home is OB1/docker/assert-rls-force.sh, INSIDE the directory that compose mounts
# as the migrations source, so one bind mount carries both the script and the SQL it derives
# from. It sits here in ai-stack because OB1 is a pinned submodule and moving it is a gitlink
# bump - a gated promotion, which H2 is not. See the promotion note at the bottom.
#
# ------------------------------------------------------------------------------------------
# (a) THE GOVERNED SET IS DERIVED, NOT HAND-LISTED - and the hand-list was ALREADY WRONG
# ------------------------------------------------------------------------------------------
# PLAN.md C.9 H2 says "the nine governed tables". Measured on the live openbrain-db
# 2026-08-31: SEVENTEEN tables carry relforcerowsecurity. 180-init-agent-memory-rls.sql
# governs nine; 200-init-graph-plane-rls.sql, landed a day later, governs eight more
# (thought_entities, entity_extraction_queue, thought_edges, idea_revisions, entities, edges,
# source_entities, consolidation_log). An assertion written to the plan's sentence would have
# passed a database with the entire graph plane unprotected. That is not a hypothetical about
# a tenth table; it is the eighth through seventeenth, already shipped.
#
# So the set is derived from the ONE place that defines it: a table is governed IF AND ONLY IF
# a migration declares `ALTER TABLE <t> FORCE ROW LEVEL SECURITY` on it. Adding a governed
# table means adding that line, and this script covers it with no edit. Removing one means
# deleting the line, which is a reviewable diff in a file named `*-rls.sql`.
#
# WHAT THE DERIVATION SOURCE IS, AND WHY IT IS THE WHOLE DIRECTORY. Two directories are read:
#
#   $RLS_MIGRATIONS_DIR  (default /opt/ob-migrations)           - the migration SOURCE
#                          directory, mounted whole. This is the authority.
#   $RLS_INITDB_DIR      (default /docker-entrypoint-initdb.d)  - the chain a FRESH volume
#                          actually runs.
#
# Deriving from the initdb chain alone is the vacuous green this whole effort keeps finding:
# unmount `200-init-graph-plane-rls.sql` and the chain declares nothing about the graph plane,
# so an assertion reading only the chain finds nothing to check and reports success. Reading
# the whole source directory means a table stops being governed only when its migration is
# DELETED FROM THE REPO. The two sets are also COMPARED: a table declared in the source
# directory but absent from the chain is a violation in its own right, because the next fresh
# volume comes up without it.
#
# WHEN THE SET GROWS: nothing here changes. When it shrinks: only by deleting a migration.
# WHAT THIS CANNOT SEE: a table that is governed in intent but whose migration never wrote a
# FORCE line. There is no evidence of such a table anywhere for this script to read, and
# inventing a second list to catch it would reintroduce exactly the defect above. That is the
# stated, accepted limit of the derivation.
#
# ------------------------------------------------------------------------------------------
# (b) relforcerowsecurity, NOT relrowsecurity
# ------------------------------------------------------------------------------------------
# They are different guarantees. relrowsecurity=t with relforcerowsecurity=f still exempts the
# TABLE OWNER from every policy, and these tables are owned by `postgres`. That exact pair -
# `agent_memories: relrowsecurity = t, relforcerowsecurity = f` - is the measurement recorded
# at the top of init-agent-memory-rls.sql as the thing four previous rounds guarded around.
# Both columns are checked and reported separately; either one being false fails the boot.
#
# ------------------------------------------------------------------------------------------
# (d) "COULD NOT CHECK" IS NOT "PASSED"
# ------------------------------------------------------------------------------------------
# Every green in this effort that turned out to be vacuous had the shape "the check could not
# run, so nothing failed". Every path out of this script that did not READ THE CATALOGUE exits
# non-zero: psql missing, connection refused, wrong credentials, query error, a short result
# set, an unreadable or empty migrations directory, and A DERIVED SET OF ZERO TABLES. A
# boundary that governs nothing is not a passing state; it is a script that has been defeated.
#
# EXIT CODES:  0 = every governed table is FORCEd.
#              1 = VIOLATION - at least one governed table is not.
#              3 = CANNOT CHECK - see above. Treated identically by the healthcheck; kept
#                  distinct so an operator can tell "unprotected" from "unreadable".
set -u

MIG_DIR="${RLS_MIGRATIONS_DIR:-/opt/ob-migrations}"
CHAIN_DIR="${RLS_INITDB_DIR:-/docker-entrypoint-initdb.d}"
DB="${POSTGRES_DB:-openbrain}"
DBUSER="${POSTGRES_USER:-postgres}"
PSQL_BIN="${PSQL_BIN:-psql}"

say() { echo "assert-rls-force: $*" >&2; }
die3() {
  say "CANNOT CHECK - $*"
  say "CANNOT CHECK IS NOT PASSED. Refusing to report healthy."
  exit 3
}

# ------------------------------------------------------------------------------------------
# Derive: every table any migration declares FORCE ROW LEVEL SECURITY on.
#
# The whole file set is lowercased, line comments stripped, and folded to a single
# whitespace-squeezed stream BEFORE matching, so a declaration split across lines is still
# found and one inside a `--` comment is not. `NO FORCE` (the revert files) cannot match: the
# pattern requires the identifier to be immediately followed by `force`, and in
# `ALTER TABLE public.t NO FORCE ...` the token after the identifier is `no`. revert*.sql is
# excluded by filename as well.
# ------------------------------------------------------------------------------------------
scan_dir() {
  d="$1"
  [ -d "$d" ] || return 1
  find "$d" -maxdepth 1 -type f -name '*.sql' ! -name '*revert*' -exec cat {} + 2>/dev/null \
    | tr 'A-Z' 'a-z' \
    | sed 's/--.*$//' \
    | tr '\n\t' '  ' \
    | tr -s ' ' \
    | grep -oE 'alter table (only )?(public\.)?[a-z_][a-z0-9_]* force row level security' \
    | sed -E 's/^alter table (only )?(public\.)?([a-z_][a-z0-9_]*) force row level security$/\3/' \
    | sort -u
}

command -v "$PSQL_BIN" >/dev/null 2>&1 || die3 "no psql on PATH ($PSQL_BIN)"
[ -d "$MIG_DIR" ] || die3 "migrations source directory $MIG_DIR is not a directory (nothing to derive the governed set FROM)"

DECLARED="$(scan_dir "$MIG_DIR")" || die3 "could not read $MIG_DIR"
CHAIN="$(scan_dir "$CHAIN_DIR" || true)"

DECL_N=$(printf '%s\n' "$DECLARED" | grep -c . || true)
[ "$DECL_N" -gt 0 ] || die3 "derived ZERO governed tables from $MIG_DIR. A boundary that governs nothing is not a pass - either the migrations are not mounted, or the FORCE declarations are gone."

# Required = declared-in-source UNION declared-in-chain. Union, not intersection: neither
# source may shrink the obligation.
REQUIRED="$(printf '%s\n%s\n' "$DECLARED" "$CHAIN" | grep . | sort -u)"
REQ_N=$(printf '%s\n' "$REQUIRED" | grep -c . || true)

# Declared in the source directory but NOT in the initdb chain: this database may be fine
# today, and the next fresh volume will not be. That is precisely the H2 failure, one restore
# early.
NOT_IN_CHAIN="$(printf '%s\n' "$DECLARED" | grep . | while read -r t; do
  printf '%s\n' "$CHAIN" | grep -qx "$t" || echo "$t"
done)"

QUOTED="$(printf '%s\n' "$REQUIRED" | sed "s/.*/'&'/" | tr '\n' ',' | sed 's/,$//')"

SQL="SELECT t.n,
            (c.oid IS NOT NULL)::text,
            COALESCE(c.relrowsecurity,false)::text,
            COALESCE(c.relforcerowsecurity,false)::text
       FROM unnest(ARRAY[$QUOTED]::text[]) AS t(n)
       LEFT JOIN pg_class c
         ON c.relname = t.n
        AND c.relnamespace = 'public'::regnamespace
        AND c.relkind = 'r'
      ORDER BY 1;"

ROWS="$("$PSQL_BIN" -X -q -At -F'|' -v ON_ERROR_STOP=1 -U "$DBUSER" -d "$DB" -c "$SQL" 2>&1)"
RC=$?
[ $RC -eq 0 ] || die3 "psql exited $RC: $(printf '%s' "$ROWS" | tr '\n' ' ')"

GOT_N=$(printf '%s\n' "$ROWS" | grep -c '|' || true)
[ "$GOT_N" -eq "$REQ_N" ] || die3 "asked the catalogue about $REQ_N tables and got $GOT_N rows back - a short answer is not an answer"

MISSING=""
NOFORCE=""
NORLS=""
for line in $ROWS; do
  n=$(printf '%s' "$line" | cut -d'|' -f1)
  present=$(printf '%s' "$line" | cut -d'|' -f2)
  rls=$(printf '%s' "$line" | cut -d'|' -f3)
  force=$(printf '%s' "$line" | cut -d'|' -f4)
  if [ "$present" != "true" ]; then
    MISSING="$MISSING $n"
    continue
  fi
  [ "$force" = "true" ] || NOFORCE="$NOFORCE $n"
  [ "$rls" = "true" ] || NORLS="$NORLS $n"
done

if [ -z "$MISSING" ] && [ -z "$NOFORCE" ] && [ -z "$NORLS" ] && [ -z "$NOT_IN_CHAIN" ]; then
  say "OK - $REQ_N governed tables, all relforcerowsecurity=true (derived from $MIG_DIR)"
  exit 0
fi

# ------------------------------------------------------------------------------------------
# (c) The failure must be impossible to miss by someone NOT looking for it.
# Three channels, because the healthcheck's own stderr is only visible to `docker inspect`:
#   1. this stderr        -> docker inspect --format '{{json .State.Health}}' openbrain-db
#   2. the POSTGRES SERVER LOG via RAISE WARNING -> `docker logs openbrain-db`, which is the
#      first thing anyone looks at, and it repeats on every probe deliberately
#   3. the non-zero exit  -> container marked unhealthy -> nine dependents refuse to start
# ------------------------------------------------------------------------------------------
MSG="EXPOSURE BOUNDARY NOT ASSERTED."
[ -n "$NOFORCE" ] && MSG="$MSG relforcerowsecurity=false on:${NOFORCE}."
[ -n "$NORLS" ] && MSG="$MSG row level security DISABLED on:${NORLS}."
[ -n "$MISSING" ] && MSG="$MSG governed table absent from the database:${MISSING}."
[ -n "$NOT_IN_CHAIN" ] && MSG="$MSG declared in $MIG_DIR but NOT in the initdb chain, so the next fresh volume comes up unprotected: $(printf '%s' "$NOT_IN_CHAIN" | tr '
' ' ')."
MSG="$MSG Apply the *-rls.sql migrations (agent-memory-plane/PROMOTION-RUNBOOK.md)."

say "$MSG"
ESC=$(printf '%s' "$MSG" | sed "s/'/''/g")
"$PSQL_BIN" -X -q -At -U "$DBUSER" -d "$DB" \
  -c "DO \$do\$ BEGIN RAISE WARNING '[assert-rls-force] %', '$ESC'; END \$do\$;" >/dev/null 2>&1 || true
exit 1

# ------------------------------------------------------------------------------------------
# PROMOTION (NOT part of H2; H2 builds and proves it). What landing this requires:
#   1. Move this file to OB1/docker/assert-rls-force.sh (LF endings - it is executed by the
#      Debian sh inside the container), commit in OB1, push, bump the ai-stack gitlink per
#      CLAUDE.md.
#   2. In OB1/docker/docker-compose.yml, openbrain-db:
#        volumes:  + - ./:/opt/ob-migrations:ro
#        healthcheck.test:
#          ["CMD-SHELL", "pg_isready -U postgres -d openbrain && sh /opt/ob-migrations/assert-rls-force.sh"]
#        start_period: 20s -> 180s   (a cold volume runs a 28-file chain; until it finishes
#                                     the assertion correctly reports NOT protected, and that
#                                     must not burn the retry budget)
#   3. scripts/checks/stack-watchdog.ps1 treats "unhealthy" as "restart it". A restart does not
#      apply a migration, so openbrain-db would churn. Either exclude openbrain-db from the
#      repair targets when the health output names this script, or accept the churn as the
#      alarm. DECIDE THIS BEFORE PROMOTING - an unbounded restart loop is a different outage
#      from a refusal to start.
#   4. The nine `condition: service_healthy` dependents are what makes this a refusal rather
#      than a log line. Anything added later that talks to openbrain-db WITHOUT that condition
#      is outside the gate.
