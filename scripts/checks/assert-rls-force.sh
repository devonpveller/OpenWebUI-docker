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
# WHERE THE REFUSAL STOPS, MEASURED (drill section 9-RED). A docker healthcheck gates
# DEPENDENTS; it does not gate the socket. An unhealthy postgres still answers anyone who
# connects to it directly - verified by connecting to one. So this is a hard refusal at the
# BOOT / DEPENDENCY EDGE, which is exactly the event H2 names (a restore, a rebuild, a
# promotion where the runbook was skipped), and for a database that goes bad WHILE RUNNING it
# is an alarm plus a refusal of the next dependent start. Anything stronger for the running
# case means revoking CONNECT or stopping the container, which trades a disclosure risk for an
# unattended self-inflicted outage; that is an operator decision, not this script's.
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
# So the set is derived from the migrations. THE DERIVATION IS NOT THE ONLY AUTHORITY, AND
# SAYING IT WAS IS THE BUG THIS ROUND FIXES. An earlier draft of this header claimed a table
# is governed "IF AND ONLY IF a migration declares FORCE on it", and then implemented that
# claim as one `grep -oE` over a lowercased stream. A verifier wrote a single migration
# declaring three tables in three forms PostgreSQL accepts - ALTER TABLE public.plain_tbl,
# ALTER TABLE IF EXISTS public.ifexists_tbl, ALTER TABLE public."NeedsQuote" - and the grep
# derived ONE. Two explicitly-governed tables were dropped on the floor and the boot assertion
# reported healthy. A parser that silently narrows the governed set is worse than no parser,
# because it produces a green.
#
# Two layers answer that, and NEITHER of them is a longer regex:
#
#   1. THE PARSE HAS A RESIDUE CHANNEL. The scanner is a tokenizer, not a pattern: it strips
#      comments (including /* */, which the old grep did not - a FORCE inside a block comment
#      was FALSELY INCLUDED), consumes string and dollar-quoted literals, splits on ";", and
#      walks the ALTER TABLE grammar token by token. Every statement that MENTIONS
#      "force row level security" and does NOT parse is emitted as UNPARSED and is a hard
#      failure. Same for the phrase appearing inside a string literal or a DO $$ ... $$ body
#      (dynamic SQL: EXECUTE format('ALTER TABLE %I FORCE ...')), which cannot be resolved
#      without running it. A form this parser cannot handle now STOPS THE BOOT instead of
#      shrinking the governed set. That is the property that matters; the grammar it happens
#      to cover is the part that can safely grow later.
#
#   2. THE CATALOGUE IS CROSS-CHECKED FOR COMPLETENESS. Every relation in the database with
#      relforcerowsecurity=true must appear in the derived set. If the catalogue reports a
#      FORCEd table the migrations did not declare, the derivation is not the authority it
#      claims to be - either the parser missed a form, or the chain ran a .sh this parser does
#      not read, or someone applied FORCE by hand. All three mean the same thing: THE GOVERNED
#      SET IS UNKNOWN, so the answer is CANNOT CHECK, not OK. The old zero-guard only caught a
#      set that shrank to zero; this catches 3 -> 1.
#
# The two directions are different failures and are reported differently:
#   declared but not FORCEd in the DB  -> exit 1, VIOLATION (the boundary is down)
#   FORCEd in the DB but not declared  -> exit 3, CANNOT CHECK (the derivation is incomplete)
#
# WHAT THE DERIVATION SOURCE IS, AND WHY IT IS THE WHOLE DIRECTORY. Two directories are read:
#
#   $RLS_MIGRATIONS_DIR  (default /opt/ob-migrations)           - the migration SOURCE
#                          directory, mounted whole. This is the authority. Read RECURSIVELY:
#                          a migration in a subdirectory counts.
#   $RLS_INITDB_DIR      (default /docker-entrypoint-initdb.d)  - the chain a FRESH volume
#                          actually runs. Read at DEPTH 1 ONLY, because that is exactly what
#                          the postgres entrypoint executes; a .sql in a subdirectory of the
#                          chain is NOT run, and counting it as present would hide a real
#                          "declared but not in the chain" violation.
#
# Deriving from the initdb chain alone is the vacuous green this whole effort keeps finding:
# unmount `200-init-graph-plane-rls.sql` and the chain declares nothing about the graph plane,
# so an assertion reading only the chain finds nothing to check and reports success. Reading
# the whole source directory means a table stops being governed only when its migration is
# DELETED FROM THE REPO. The two sets are also COMPARED: a table declared in the source
# directory but absent from the chain is a violation in its own right, because the next fresh
# volume comes up without it.
#
# File types read: *.sql, and *.sql.gz / *.sql.xz / *.sql.zst, which the postgres entrypoint
# DOES execute and an earlier draft ignored. If a compressed migration is present and its
# decompressor is not installed, that is CANNOT CHECK, not a pass.
#
# ------------------------------------------------------------------------------------------
# THE LIMITS THAT REMAIN. There is more than one, and the earlier draft claimed there was one.
# ------------------------------------------------------------------------------------------
#   L1. A table governed IN INTENT whose migration never wrote a FORCE line is invisible here.
#       Nothing in the repo records such an intent for this script to read, and inventing a
#       second hand-list to catch it would reintroduce the defect above.
#   L2. `*revert*` files are excluded BY FILENAME. They contain NO FORCE for tables the init
#       migrations FORCE, so scanning them would make every governed table ambiguous. This is
#       the one filename-shaped rule left, and it is load-bearing in a safe direction:
#       renaming a revert file to something without "revert" in it makes the governed set
#       ambiguous, which fails loudly (exit 3) rather than quietly.
#   L3. A .sh in the initdb chain that applies FORCE through psql is not parsed - it is
#       arbitrary shell, and this script's own text (full of the phrase) lives in the same
#       directory. Layer 2 covers it: the tables such a script FORCEs appear in the catalogue
#       and not in the derived set, which is exit 3.
#   L4. E'...' escape-string literals with a backslash-escaped quote are not tracked. The
#       failure mode is a mis-split statement, which surfaces as UNPARSED (loud), not as a
#       dropped table.
#   L5. Whitespace inside a quoted identifier is normalised to single spaces, so two
#       identifiers differing only in internal whitespace run-length would collide.
#   L6. Only the CURRENT state of the mounted migration set is read. It cannot tell you a
#       FORCE line was deleted last week; it can only tell you the boundary is down now.
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
# (e) THE CATALOGUE LOOKUP MUST NOT INVENT A RED EITHER
# ------------------------------------------------------------------------------------------
# An earlier draft pinned `c.relkind = 'r'` and `c.relnamespace = 'public'::regnamespace` in
# the lookup. A verifier created `public.part_parent PARTITION BY RANGE` (relkind `p`),
# correctly ENABLEd and FORCEd, declared in a migration in both directories - and got
# "governed table absent from the database", exit 1, forever. Wired as designed that is
# openbrain-db never going healthy and nine dependents refusing to start: an unbootable stack
# from a WRONG diagnosis. Fail-closed is right; fail-closed on a false premise is an outage.
#
# So the lookup is by (declared schema, declared name) with NO relkind filter - partitioned
# tables (`p`) and foreign tables (`f`) carry relforcerowsecurity exactly like ordinary ones -
# and the relkind that was found is REPORTED, so a declaration that resolved to something
# which cannot carry RLS is legible rather than mysterious. The schema comes from the
# declaration itself (`auth.x` looks in `auth`; the live database already has an `auth`
# schema), defaulting to `public` only when the declaration is unqualified.
#
# ------------------------------------------------------------------------------------------
# (d) "COULD NOT CHECK" IS NOT "PASSED"
# ------------------------------------------------------------------------------------------
# Every green in this effort that turned out to be vacuous had the shape "the check could not
# run, so nothing failed". Every path out of this script that did not READ THE CATALOGUE exits
# non-zero: psql or awk missing, connection refused, wrong credentials, query error, a short
# result set, an unreadable or empty migrations directory, A DERIVED SET OF ZERO TABLES, a
# statement the parser could not understand, and a FORCEd table the parser never derived. A
# boundary that governs nothing - or governs an unknown set - is not a passing state.
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
AWK_BIN="${AWK_BIN:-awk}"

TAB=$(printf '\t')
WORKDIR=""

say() { echo "assert-rls-force: $*" >&2; }
cleanup() { [ -n "$WORKDIR" ] && rm -rf "$WORKDIR"; return 0; }
die3() {
  say "CANNOT CHECK - $*"
  say "CANNOT CHECK IS NOT PASSED. Refusing to report healthy."
  cleanup
  exit 3
}

# ------------------------------------------------------------------------------------------
# THE PARSER. Not a pattern - a tokenizer with a residue channel.
#
# Per input file: strip -- line comments and NESTED /* */ block comments; consume single
# quoted and dollar-quoted literals whole (a literal that CONTAINS the phrase is reported as
# dynamic SQL, because EXECUTE format('ALTER TABLE %I FORCE ...') cannot be resolved without
# running it); track quoted identifiers so their case survives; split on ";".
#
# Any statement that mentions "force row level security" is then walked token by token against
#   ALTER TABLE [IF EXISTS] [ONLY] [schema.]name [*] [NO] FORCE ROW LEVEL SECURITY
# and emitted as F (governed) or N (revoked). ANY SUCH STATEMENT THAT DOES NOT WALK CLEANLY IS
# EMITTED AS U - unparsed - which is a hard failure upstream. That is the whole point: a form
# this parser does not know must be DETECTED, never dropped.
#
# Output, one record per line, tab separated:
#   F <schema> <table>     declares FORCE
#   N <schema> <table>     declares NO FORCE
#   U <text>               a FORCE-mentioning statement this parser does not understand
#   D <text>               the phrase inside a string / dollar-quoted body: dynamic SQL
# ------------------------------------------------------------------------------------------
AWK_PARSE=$(cat <<'AWK_EOF'
BEGIN {
  SQ = sprintf("%c", 39); DQ = sprintf("%c", 34)
  CLS = "[-/;$" SQ DQ "]"
  FN = ""; state = 0; depth = 0; buf = ""; lit = ""; tag = ""
}
function app(x) {
  gsub(/[ \t\r\n]+/, " ", x)
  if (x == "") return
  if (substr(x, 1, 1) == " " && (buf == "" || substr(buf, length(buf), 1) == " ")) x = substr(x, 2)
  buf = buf x
}
function addsp() { app(" ") }
function trunc(x) { if (length(x) > 180) return substr(x, 1, 177) "..."; return x }
function emit(k, v) { print k "\t" v }
function checklit(x,   y) {
  y = x; gsub(/[ \t\r\n]+/, " ", y)
  if (index(tolower(y), "force row level security") > 0) emit("D", trunc(y) "   [in " FN "]")
}
function tokenize(b,   n, i, c, j, v) {
  ntok = 0; n = length(b); i = 1
  while (i <= n) {
    c = substr(b, i, 1)
    if (c == " ") { i++; continue }
    if (c == DQ) {
      v = ""; i++
      while (i <= n) {
        c = substr(b, i, 1)
        if (c == DQ) { if (substr(b, i + 1, 1) == DQ) { v = v DQ; i += 2; continue } i++; break }
        v = v c; i++
      }
      ntok++; TOKV[ntok] = v; TOKT[ntok] = "Q"; continue
    }
    if (c ~ /[A-Za-z0-9_$]/) {
      j = i
      while (j <= n && substr(b, j, 1) ~ /[A-Za-z0-9_$]/) j++
      ntok++; TOKV[ntok] = substr(b, i, j - i); TOKT[ntok] = "W"; i = j; continue
    }
    ntok++; TOKV[ntok] = c; TOKT[ntok] = "P"; i++
  }
}
function kw(i, w) { return (TOKT[i] == "W" && tolower(TOKV[i]) == w) }
function isident(i) { return (TOKT[i] == "Q" || (TOKT[i] == "W" && TOKV[i] ~ /^[A-Za-z_][A-Za-z0-9_$]*$/)) }
function identval(i) { if (TOKT[i] == "Q") return TOKV[i]; return tolower(TOKV[i]) }
function parse(b,   i, sch, tbl, nof) {
  tokenize(b)
  if (ntok < 6) return ""
  if (!kw(1, "alter") || !kw(2, "table")) return ""
  i = 3
  if (kw(i, "if") && kw(i + 1, "exists")) i += 2
  if (kw(i, "only")) i++
  if (!isident(i)) return ""
  tbl = identval(i); sch = "public"; i++
  if (TOKT[i] == "P" && TOKV[i] == ".") {
    i++
    if (!isident(i)) return ""
    sch = tbl; tbl = identval(i); i++
  }
  if (TOKT[i] == "P" && TOKV[i] == "*") i++
  nof = 0
  if (kw(i, "no")) { nof = 1; i++ }
  if (!kw(i, "force") || !kw(i + 1, "row") || !kw(i + 2, "level") || !kw(i + 3, "security")) return ""
  if (i + 4 <= ntok) return ""
  if (nof) return "N\t" sch "\t" tbl
  return "F\t" sch "\t" tbl
}
function flush(   b, low, r) {
  b = buf; buf = ""
  sub(/^ +/, "", b); sub(/ +$/, "", b)
  if (b == "") return
  low = tolower(b)
  if (index(low, "force row level security") == 0) return
  if (substr(low, 1, 12) != "alter table ") { emit("U", trunc(b) "   [in " FN "]"); return }
  r = parse(b)
  if (r == "") emit("U", trunc(b) "   [in " FN "]")
  else print r
}
function do0(s, n, i,   rest, p, c, c2, j) {
  rest = substr(s, i)
  p = match(rest, CLS)
  if (p == 0) { app(substr(s, i)); return n + 1 }
  p = i + p - 1
  if (p > i) app(substr(s, i, p - i))
  c = substr(s, p, 1); c2 = substr(s, p + 1, 1)
  if (c == "-") { if (c2 == "-") { addsp(); return n + 1 } app("-"); return p + 1 }
  if (c == "/") { if (c2 == "*") { state = 1; depth = 1; return p + 2 } app("/"); return p + 1 }
  if (c == ";") { flush(); return p + 1 }
  if (c == SQ) { state = 2; lit = ""; return p + 1 }
  if (c == DQ) { app(DQ); state = 4; return p + 1 }
  if (c == "$") {
    j = p + 1
    while (j <= n && substr(s, j, 1) ~ /[A-Za-z0-9_]/) j++
    if (j <= n && substr(s, j, 1) == "$") { tag = substr(s, p, j - p + 1); state = 3; lit = ""; return j + 1 }
    app("$"); return p + 1
  }
  app(c); return p + 1
}
function do1(s, n, i,   a, b) {
  a = index(substr(s, i), "*/"); b = index(substr(s, i), "/*")
  if (b > 0 && (a == 0 || b < a)) { depth++; return i + b + 1 }
  if (a == 0) return n + 1
  depth--
  if (depth <= 0) { state = 0; addsp() }
  return i + a + 1
}
function do2(s, n, i,   p) {
  p = index(substr(s, i), SQ)
  if (p == 0) { lit = lit substr(s, i); return n + 1 }
  p = i + p - 1
  lit = lit substr(s, i, p - i)
  if (substr(s, p + 1, 1) == SQ) { lit = lit SQ; return p + 2 }
  state = 0; checklit(lit); app(SQ SQ); return p + 1
}
function do3(s, n, i,   p) {
  p = index(substr(s, i), tag)
  if (p == 0) { lit = lit substr(s, i); return n + 1 }
  p = i + p - 1
  lit = lit substr(s, i, p - i)
  state = 0; checklit(lit); app(SQ SQ); return p + length(tag)
}
function do4(s, n, i,   p) {
  p = index(substr(s, i), DQ)
  if (p == 0) { app(substr(s, i)); return n + 1 }
  p = i + p - 1
  if (p > i) app(substr(s, i, p - i))
  if (substr(s, p + 1, 1) == DQ) { app(DQ DQ); return p + 2 }
  app(DQ); state = 0; return p + 1
}
function process(s,   n, i) {
  n = length(s); i = 1
  while (i <= n) {
    if (state == 0) i = do0(s, n, i)
    else if (state == 1) i = do1(s, n, i)
    else if (state == 2) i = do2(s, n, i)
    else if (state == 3) i = do3(s, n, i)
    else i = do4(s, n, i)
  }
}
function finfile(   w) {
  if (FN == "") return
  if (state != 0) {
    w = "quoted identifier"
    if (state == 1) w = "block comment"
    else if (state == 2) w = "string literal"
    else if (state == 3) w = "dollar-quoted block"
    emit("U", "file ends inside an unterminated " w " - it cannot be parsed reliably   [in " FN "]")
  } else flush()
  FN = ""
}
FNR == 1 { finfile(); FN = FILENAME; state = 0; depth = 0; buf = ""; lit = ""; tag = "" }
{ process($0 "\n") }
END { finfile() }
AWK_EOF
)

# ------------------------------------------------------------------------------------------
# scan_dir <dir> <recursive|flat> - emit parser records for every migration file under <dir>.
# Compressed migrations are decompressed into a scratch dir first; a missing decompressor is
# CANNOT CHECK, never a silent skip.
# ------------------------------------------------------------------------------------------
scan_dir() {
  _d="$1"
  if [ "$2" = "flat" ]; then _depth="-maxdepth 1"; else _depth=""; fi

  # shellcheck disable=SC2086
  _comp=$(find "$_d" $_depth -type f ! -name '*revert*' \
            \( -name '*.sql.gz' -o -name '*.sql.xz' -o -name '*.sql.zst' \) 2>/dev/null)
  if [ -n "$_comp" ]; then
    if [ -z "$WORKDIR" ]; then
      WORKDIR=$(mktemp -d 2>/dev/null) || die3 "compressed migrations are present under $_d and mktemp -d failed, so they cannot be read"
    fi
    _n=0
    _rc=0
    for _f in $_comp; do
      _n=$((_n + 1))
      case "$_f" in
        *.gz)  command -v gzip >/dev/null 2>&1 || { _rc=21; break; }
               gzip -dc "$_f" > "$WORKDIR/c$_n.sql" || { _rc=24; break; } ;;
        *.xz)  command -v xz >/dev/null 2>&1 || { _rc=22; break; }
               xz -dc "$_f" > "$WORKDIR/c$_n.sql" || { _rc=24; break; } ;;
        *.zst) command -v zstd >/dev/null 2>&1 || { _rc=23; break; }
               zstd -dcq "$_f" > "$WORKDIR/c$_n.sql" || { _rc=24; break; } ;;
      esac
    done
    [ "$_rc" -eq 0 ] || die3 "a compressed migration under $_d could not be read (rc=$_rc; 21/22/23 = gzip/xz/zstd not installed). The postgres entrypoint WOULD run it, so this is not a file to skip."
    find "$WORKDIR" -type f -name '*.sql' -exec "$AWK_BIN" "$AWK_PARSE" {} +
    rm -f "$WORKDIR"/c*.sql 2>/dev/null
  fi

  # shellcheck disable=SC2086
  find "$_d" $_depth -type f ! -name '*revert*' -name '*.sql' -exec "$AWK_BIN" "$AWK_PARSE" {} + 2>/dev/null
}

command -v "$PSQL_BIN" >/dev/null 2>&1 || die3 "no psql on PATH ($PSQL_BIN)"
command -v "$AWK_BIN"  >/dev/null 2>&1 || die3 "no awk on PATH ($AWK_BIN) - the migration parser cannot run"
[ -d "$MIG_DIR" ] || die3 "migrations source directory $MIG_DIR is not a directory (nothing to derive the governed set FROM)"

SRC_REC="$(scan_dir "$MIG_DIR" recursive)"
if [ -d "$CHAIN_DIR" ]; then CHAIN_REC="$(scan_dir "$CHAIN_DIR" flat)"; else CHAIN_REC=""; fi
ALL_REC="$(printf '%s\n%s\n' "$SRC_REC" "$CHAIN_REC")"

# ---- LAYER 1: the parse residue. A form we could not read is a failure, not an omission. ---
BAD="$(printf '%s\n' "$ALL_REC" | grep -E "^[UD]$TAB" | sort -u)"
if [ -n "$BAD" ]; then
  BAD_N=$(printf '%s\n' "$BAD" | grep -c .)
  say "CANNOT CHECK - $BAD_N declaration(s) mention FORCE ROW LEVEL SECURITY in a form this parser does not resolve. Narrowing the governed set silently is how a boundary check goes vacuous, so this stops the boot instead:"
  printf '%s\n' "$BAD" | sed "s/^U$TAB/  UNPARSED:    /; s/^D$TAB/  DYNAMIC SQL: /" >&2
  say "CANNOT CHECK IS NOT PASSED. Refusing to report healthy."
  cleanup
  exit 3
fi

decl_set() { printf '%s\n' "$1" | grep "^F$TAB" | cut -f2,3 | sort -u; }
norv_set() { printf '%s\n' "$1" | grep "^N$TAB" | cut -f2,3 | sort -u; }

DECLARED="$(decl_set "$SRC_REC")"
CHAIN_DECL="$(decl_set "$CHAIN_REC")"
ALL_DECL="$(printf '%s\n%s\n' "$DECLARED" "$CHAIN_DECL" | grep . | sort -u)"

# A name declared BOTH FORCE and NO FORCE inside the scanned set is order-dependent, so the
# governed set is not knowable from the files alone.
AMBIG="$(norv_set "$ALL_REC" | grep . | while IFS= read -r t; do
  printf '%s\n' "$ALL_DECL" | grep -qxF "$t" && printf '%s\n' "$t"
done | sort -u)"
[ -z "$AMBIG" ] || die3 "declared BOTH FORCE and NO FORCE in the scanned migrations, so which wins depends on file order: $(printf '%s' "$AMBIG" | sed "s/$TAB/./" | tr '\n' ' ')"

DECL_N=$(printf '%s\n' "$DECLARED" | grep -c . || true)
[ "$DECL_N" -gt 0 ] || die3 "derived ZERO governed tables from $MIG_DIR. A boundary that governs nothing is not a pass - either the migrations are not mounted, or the FORCE declarations are gone."

# Required = declared-in-source UNION declared-in-chain. Union, not intersection: neither
# source may shrink the obligation.
REQUIRED="$ALL_DECL"
REQ_N=$(printf '%s\n' "$REQUIRED" | grep -c . || true)

# Declared in the source directory but NOT in the initdb chain: this database may be fine
# today, and the next fresh volume will not be. That is precisely the H2 failure, one restore
# early.
NOT_IN_CHAIN="$(printf '%s\n' "$DECLARED" | grep . | while IFS= read -r t; do
  printf '%s\n' "$CHAIN_DECL" | grep -qxF "$t" || printf '%s\n' "$t"
done)"

VALUES="$(printf '%s\n' "$REQUIRED" | grep . | while IFS="$TAB" read -r s t; do
  se=$(printf '%s' "$s" | sed "s/'/''/g")
  te=$(printf '%s' "$t" | sed "s/'/''/g")
  printf "('%s','%s')," "$se" "$te"
done | sed 's/,$//')"

# The lookup carries NO relkind filter and uses the DECLARED schema - see (e) above. The
# second leg is the completeness cross-check: every relation the catalogue reports as FORCEd.
SQL="WITH d(s,n) AS (VALUES $VALUES)
     SELECT 'D', d.s, d.n, (c.oid IS NOT NULL)::text,
            COALESCE(c.relkind::text,'-'),
            COALESCE(c.relrowsecurity,false)::text,
            COALESCE(c.relforcerowsecurity,false)::text
       FROM d
       LEFT JOIN pg_namespace ns ON ns.nspname = d.s
       LEFT JOIN pg_class c ON c.relname = d.n AND c.relnamespace = ns.oid
     UNION ALL
     SELECT 'C', n.nspname, c.relname, '', '', '', ''
       FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE c.relforcerowsecurity;"

ROWS="$("$PSQL_BIN" -X -q -At -F"$TAB" -v ON_ERROR_STOP=1 -U "$DBUSER" -d "$DB" -c "$SQL" 2>&1)"
RC=$?
[ $RC -eq 0 ] || die3 "psql exited $RC: $(printf '%s' "$ROWS" | tr '\n' ' ')"

DROWS="$(printf '%s\n' "$ROWS" | grep "^D$TAB" || true)"
CROWS="$(printf '%s\n' "$ROWS" | grep "^C$TAB" | cut -f2,3 | sort -u || true)"

GOT_N=$(printf '%s\n' "$DROWS" | grep -c . || true)
[ "$GOT_N" -eq "$REQ_N" ] || die3 "asked the catalogue about $REQ_N tables and got $GOT_N rows back - a short answer is not an answer"

# ---- LAYER 2: completeness. A FORCEd relation nobody declared means the parse is incomplete -
BLIND="$(printf '%s\n' "$CROWS" | grep . | while IFS= read -r t; do
  printf '%s\n' "$REQUIRED" | grep -qxF "$t" || printf '%s\n' "$t"
done)"
if [ -n "$BLIND" ]; then
  BLIND_N=$(printf '%s\n' "$BLIND" | grep -c .)
  say "CANNOT CHECK - the catalogue reports $BLIND_N FORCEd relation(s) the migration parse did NOT derive:$(printf '%s' "$BLIND" | sed "s/$TAB/./" | tr '\n' ' ')"
  say "The derived governed set is therefore NOT the whole governed set - the parser missed a form, a .sh in the chain applied FORCE, or someone applied it by hand. Any of those means the boundary's extent is unknown, and unknown is not OK. Declare it in a *-rls.sql migration."
  say "CANNOT CHECK IS NOT PASSED. Refusing to report healthy."
  cleanup
  exit 3
fi

MISSING=""
NOFORCE=""
NORLS=""
while IFS="$TAB" read -r k s n present kind rls force; do
  [ "$k" = "D" ] || continue
  disp="$s.$n"
  if [ "$present" != "true" ]; then
    MISSING="$MISSING $disp"
    continue
  fi
  [ "$force" = "true" ] || NOFORCE="$NOFORCE $disp(relkind=$kind)"
  [ "$rls" = "true" ] || NORLS="$NORLS $disp(relkind=$kind)"
done <<EOF
$DROWS
EOF

if [ -z "$MISSING" ] && [ -z "$NOFORCE" ] && [ -z "$NORLS" ] && [ -z "$NOT_IN_CHAIN" ]; then
  say "OK - $REQ_N governed tables, all relforcerowsecurity=true (derived from $MIG_DIR, cross-checked against every FORCEd relation in the catalogue)"
  cleanup
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
[ -n "$NOT_IN_CHAIN" ] && MSG="$MSG declared in $MIG_DIR but NOT in the initdb chain, so the next fresh volume comes up unprotected: $(printf '%s' "$NOT_IN_CHAIN" | sed "s/$TAB/./" | tr '\n' ' ')."
MSG="$MSG Apply the *-rls.sql migrations (agent-memory-plane/PROMOTION-RUNBOOK.md)."

say "$MSG"
ESC=$(printf '%s' "$MSG" | sed "s/'/''/g")
"$PSQL_BIN" -X -q -At -U "$DBUSER" -d "$DB" \
  -c "DO \$do\$ BEGIN RAISE WARNING '[assert-rls-force] %', '$ESC'; END \$do\$;" >/dev/null 2>&1 || true
cleanup
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
#   5. `healthcheck.timeout` - the parser is a token walk over the whole migration set, so the
#      runtime moved when it replaced the grep. The drill measures it on every run (section
#      10) and FAILS if it is within 2x of the 5s timeout, because a healthcheck that times
#      out is a false positive and a false positive here is an outage.
#   6. Layer 2 (the catalogue cross-check) makes a hand-applied `ALTER TABLE ... FORCE` on an
#      undeclared table fail the boot with exit 3. That is deliberate - it is how an unparsed
#      form gets detected - but it means the remedy for "I FORCEd a table by hand" is to add
#      the FORCE line to a migration, not to argue with the health probe.
