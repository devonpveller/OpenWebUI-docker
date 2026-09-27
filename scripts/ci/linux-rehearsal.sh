#!/bin/sh
# linux-rehearsal.sh - the adoption close-out's acceptance test: a stranger's Linux clone,
# followed from README.md AS WRITTEN, inside a fresh Docker-in-Docker daemon with no GPU.
#
# WHAT IT PROVES (the close-out plan's section 5, criteria 1-5; each ends in one line
# `CRITERION <n>: PASS|FAIL - <title>`):
#   1  fresh clone, frontend only - README's Linux quickstart block, run verbatim except
#      for the clone URL, ends with Open WebUI's /health 200, and only the frontend
#      plane's containers exist (counted, listed, compared with the README's own count)
#   2  enabling a product - `enable inference` names the missing keys and the file, the
#      keys are supplied, `up` refuses the GPU profile and prints numbered steps, the
#      steps are run AS PRINTED, and `up` then starts the planes in dependency order with
#      inference serving; `enable search` with stub Mullvad keys starts the plane and
#      fails only for the reason search/README.md documents (the tunnel cannot come up)
#   3  committing from Linux with git + python3 only - README's Contributing blocks run
#      verbatim; a doc-only and a Python commit succeed and print which gates ran and
#      which were skipped; a planted secret, CRLF shell script and personal identifier
#      are each refused and HEAD does not move
#   4  code repo contents - no operator journal in `git ls-files`; check_identity.py
#      --all exits 0 with no denylist
#   5  agent routing - CLAUDE.md's plan-store section is quoted and names plans, notes,
#      findings and evidence; staging documentation/notes/<x>.md is refused
# Criterion 6 (the live deployment renders unchanged) is HOST-side and read-only; it is
# not done here. Open Brain from a fresh clone is a known blocker (G19): the run records
# what `enable open-brain` says before and after the Contributing loop, as INFO lines.
#
# HOW THE README IS READ. Three fenced blocks are found by the HTML comment on the line
# before them - `<!-- rehearsal:linux-quickstart`, `<!-- rehearsal:contributing-hooks`,
# `<!-- rehearsal:contributing-checks` - each of which must occur exactly once and be
# followed (blank lines allowed) by a ```-fence. The quickstart's FIRST line must be a
# `git clone` with exactly one https:// URL; the rehearsal replaces that URL with the
# clone source (and adds `--branch <ref>` for --ref with a URL source) and changes
# nothing else - it prints the README block, the block it runs and their diff, and FAILS
# if more than that one line differs. So README drift breaks the rehearsal.
#
# TWO MODES.
#   default (outer)  host side, any POSIX sh with docker and git (Linux, Git Bash):
#                    bundles --ref from this checkout (or takes --source), starts ONE
#                    privileged docker:27-dind container labelled
#                    ai-stack.harness.owner=<owner>, installs python3 git curl in it,
#                    copies this script and the bundle in, runs `--inside` there, and
#                    removes the DinD with its volume on every exit the shell can trap.
#                    The host daemon sees one container: no port published, no host path
#                    mounted, the host's docker.sock never shared.
#   --inside         runs the rehearsal against the daemon `docker` talks to. It REFUSES
#                    unless that daemon is EMPTY (no container, no volume, no network but
#                    bridge/host/none), because every plane uses fixed container names and
#                    a stack there would be driven. At the end it removes every container,
#                    and every volume and network it created.
#
# Usage:
#   scripts/ci/linux-rehearsal.sh [--ref <commit-ish>] [--source <url|bundle>]
#       [--name <dind container>] [--owner <label value>] [--image <dind image>]
#       [--timeout <seconds>] [--keep]
#   --ref      default source: the commit to bundle from this checkout (default HEAD).
#              With --source: a branch or tag to clone (`git clone --branch`).
#   --source   clone this instead: an https:// URL, or a git bundle file whose HEAD is
#              the commit to rehearse (a URL source makes the DinD fetch it)
#   --timeout  seconds allowed for the quickstart block, whose last line waits for
#              /health with no limit of its own (default 1800)
#   --keep     leave the DinD running for inspection (docker rm -f -v <name> after)
#
# Needs network inside the DinD: image pulls (Open WebUI is about 5 GB), the OB1
# submodule from its public URL (the quickstart's --recurse-submodules), pip for the
# Contributing block, apt/pip inside the search gateway's image build, and the stub
# tunnel's dial-out to a Mullvad endpoint (it fails, which is what is asserted).
#
# Exit 0 when criteria 1-5 all PASS, 1 otherwise, 2 on a usage or setup error. The last
# line is `RESULT: PASS` or `RESULT: FAIL (...)`.

set -u
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

MODE=outer
REF=""
SOURCE=""
NAME="linux-rehearsal-$$"
OWNER=linux-rehearsal
IMAGE=docker:27-dind
TIMEOUT=1800
KEEP=0

usage() { sed -n '2,/^set -u$/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --inside) MODE=inside; shift ;;
    --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
    --source) SOURCE="${2:?--source needs a value}"; shift 2 ;;
    --name) NAME="${2:?--name needs a value}"; shift 2 ;;
    --owner) OWNER="${2:?--owner needs a value}"; shift 2 ;;
    --image) IMAGE="${2:?--image needs a value}"; shift 2 ;;
    --timeout) TIMEOUT="${2:?--timeout needs a value}"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)"; exit 2 ;;
  esac
done

now() { date +%s; }
T0=$(now)

is_url() { case "$1" in *://*|git@*) return 0 ;; *) return 1 ;; esac; }

# =====================================================================================
# OUTER: host side
# =====================================================================================
if [ "$MODE" = outer ]; then
  SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
  # Git Bash: git.exe and docker.exe are Windows programs and path conversion is off
  # (above, so /rehearsal reaches docker intact) - hand them the C:/... form.
  if command -v cygpath >/dev/null 2>&1; then SCRIPT_DIR=$(cygpath -m "$SCRIPT_DIR"); fi
  REPO=$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null) || {
    echo "not inside a git checkout: $SCRIPT_DIR"; exit 2; }
  if docker container inspect "$NAME" >/dev/null 2>&1; then
    echo "refused: a container named $NAME already exists on this daemon; pick another --name"; exit 2
  fi
  TMP=$(mktemp -d)
  if command -v cygpath >/dev/null 2>&1; then TMP=$(cygpath -m "$TMP"); fi
  # shellcheck disable=SC2329
  cleanup() {  # run by the EXIT trap
    rm -rf "$TMP"
    if [ "$KEEP" -eq 1 ]; then
      echo "# --keep: $NAME left running (docker rm -f -v $NAME when done)"
    else
      docker rm -f -v "$NAME" >/dev/null 2>&1 && echo "# removed $NAME and its volume"
    fi
  }
  trap cleanup EXIT
  trap 'exit 130' INT TERM

  INNER_ARGS="--inside --timeout $TIMEOUT"
  BUNDLE=""
  if [ -z "$SOURCE" ]; then
    SHA=$(git -C "$REPO" rev-parse --verify "${REF:-HEAD}^{commit}" 2>/dev/null) || {
      echo "--ref '${REF:-HEAD}' does not name a commit in $REPO"; exit 2; }
    # A bundle is cut from a throwaway bare repo, so $REPO's refs are never touched.
    git init -q --bare "$TMP/src.git"
    git -C "$REPO" push -q "$TMP/src.git" "$SHA:refs/heads/rehearse" || { echo "RESULT: FAIL (bundle source)"; exit 2; }
    git -C "$TMP/src.git" symbolic-ref HEAD refs/heads/rehearse
    git -C "$TMP/src.git" bundle create "$TMP/src.bundle" --all 2>/dev/null || { echo "RESULT: FAIL (bundle)"; exit 2; }
    BUNDLE="$TMP/src.bundle"
    WHAT="bundle of $SHA ($(git -C "$REPO" log -1 --format=%s "$SHA" | cut -c1-80))"
  elif is_url "$SOURCE"; then
    INNER_ARGS="$INNER_ARGS --source $SOURCE"
    [ -n "$REF" ] && INNER_ARGS="$INNER_ARGS --ref $REF"
    WHAT="$SOURCE${REF:+ @ $REF}"
  else
    [ -f "$SOURCE" ] || { echo "--source '$SOURCE' is neither a URL nor a file"; exit 2; }
    BUNDLE="$SOURCE"
    [ -n "$REF" ] && INNER_ARGS="$INNER_ARGS --ref $REF"
    WHAT="bundle $SOURCE${REF:+ @ $REF}"
  fi
  [ -n "$BUNDLE" ] && INNER_ARGS="$INNER_ARGS --source /rehearsal/src.bundle"

  echo "== linux-rehearsal: $WHAT"
  echo "   DinD $NAME ($IMAGE), label ai-stack.harness.owner=$OWNER"
  docker run --privileged -d --name "$NAME" --label "ai-stack.harness.owner=$OWNER" \
    -e DOCKER_TLS_CERTDIR= "$IMAGE" >/dev/null || { echo "RESULT: FAIL (could not start $IMAGE)"; exit 2; }
  i=0
  until docker exec "$NAME" docker info >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -ge 60 ] && { echo "RESULT: FAIL (the DinD daemon never answered)"; exit 2; }
    sleep 2
  done
  docker exec "$NAME" apk add --no-cache -q python3 git curl >/dev/null || {
    echo "RESULT: FAIL (apk add python3 git curl failed inside the DinD)"; exit 2; }
  docker exec "$NAME" mkdir -p /rehearsal
  docker exec -i "$NAME" sh -c 'cat > /rehearsal/linux-rehearsal.sh' < "$0" || { echo "RESULT: FAIL (copying the script)"; exit 2; }
  if [ -n "$BUNDLE" ]; then
    docker exec -i "$NAME" sh -c 'cat > /rehearsal/src.bundle' < "$BUNDLE" || { echo "RESULT: FAIL (copying the bundle)"; exit 2; }
  fi
  # INNER_ARGS is a word list built above:
  # shellcheck disable=SC2086
  docker exec "$NAME" sh /rehearsal/linux-rehearsal.sh $INNER_ARGS
  RC=$?
  echo "# outer: wall clock $(( $(now) - T0 ))s including the DinD's start and teardown"
  exit $RC
fi

# =====================================================================================
# INSIDE: the rehearsal itself, against the daemon `docker` talks to
# =====================================================================================
[ -n "$SOURCE" ] || { echo "--inside needs --source"; exit 2; }

say() { printf '%s\n' "$*"; }
hr() { say ""; say "== $*"; }

# per-criterion bookkeeping: C<n>_FAIL counts failed checks, C<n>_RAN counts checks
for n in 1 2 3 4 5; do eval "C${n}_FAIL=0; C${n}_RAN=0"; done
ck() {  # ck <criterion> <description> <0 = ok | nonzero = fail>
  eval "C$1_RAN=\$((C$1_RAN + 1))"
  if [ "$3" -eq 0 ]; then say "  [OK]   ($1) $2"; else say "  [FAIL] ($1) $2"; eval "C$1_FAIL=\$((C$1_FAIL + 1))"; fi
}
t() { if "$@"; then echo 0; else echo 1; fi; }   # a test's truth as 0/1, for ck
INFO=""
info() { say "  [INFO] $*"; INFO="$INFO
INFO $*"; }

# pull/build progress lines drown a transcript; the full log is kept beside it
quiet() { grep -v -E 'Pulling fs layer|Pull complete|Downloading|Download complete|Verifying Checksum|Waiting$|Extracting|Already exists|^#[0-9]+ |Pulling$|Pulled $|^ *[a-f0-9]{12} ' || true; }

# ---- 0. the daemon must be empty, and the tools present -----------------------------
hr "0. environment"
# Each listing FAILS CLOSED: a daemon that cannot be listed is not an empty one.
E_C=$(docker ps -aq 2>&1) || { echo "refused: 'docker ps -a' failed: $E_C"; exit 2; }
E_V=$(docker volume ls -q 2>&1) || { echo "refused: 'docker volume ls' failed: $E_V"; exit 2; }
E_N=$(docker network ls --format '{{.Name}}' 2>&1) || { echo "refused: 'docker network ls' failed: $E_N"; exit 2; }
EXISTING=$(printf '%s\n%s\n%s\n' "$E_C" "$E_V" "$(printf '%s\n' "$E_N" | grep -vxE 'bridge|host|none')" | grep -v '^$')
if [ -n "$EXISTING" ]; then
  say "refused: --inside drives the daemon docker talks to, and it is not empty:"
  printf '%s\n' "$EXISTING" | sed 's/^/    /'
  say "  Every plane uses fixed container names, so a stack there would be driven. Run it on a"
  say "  disposable daemon (the default outer mode makes one)."
  exit 2
fi
for tool in git python3 curl docker timeout; do
  command -v "$tool" >/dev/null 2>&1 || { say "RESULT: FAIL ($tool is not on PATH)"; exit 2; }
done
docker compose version >/dev/null 2>&1 || { say "RESULT: FAIL (no docker compose plugin)"; exit 2; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { say "RESULT: FAIL (python3 < 3.11)"; exit 2; }
RUNTIMES=$(docker info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}')
say "   $(uname -s) $(uname -m); docker $(docker version --format '{{.Server.Version}}'); $(docker compose version)"
say "   $(python3 --version); git $(git --version | awk '{print $3}'); runtimes: $RUNTIMES"
say "   pwsh on PATH: $(command -v pwsh || echo no); powershell.exe on PATH: $(command -v powershell.exe || echo no)"
WORK=/work
[ -d /rehearsal ] || WORK=$(mktemp -d)
mkdir -p "$WORK"
LOGS="$WORK/logs"; mkdir -p "$LOGS"
say "   work dir $WORK, full logs under $LOGS"

# ---- extract the README blocks ------------------------------------------------------
# extract_block <marker> <readme> <out>: the fenced block after the marker comment.
# Exit 0, or 2 = marker missing/duplicated, 3 = no fence after it, 4 = unterminated.
extract_block() {
  _c=$(grep -c -F "<!-- $1" "$2")
  [ "$_c" = 1 ] || { say "  marker '<!-- $1' occurs $_c time(s) in README.md (must be exactly 1)"; return 2; }
  awk -v m="<!-- $1" '
    state == 0 && index($0, m) == 1 { state = 1; next }
    state == 1 && /^```/ { state = 2; next }
    state == 1 && NF { bad = 3; exit }
    state == 2 && /^```/ { state = 3; exit }
    state == 2 { print }
    END { if (bad) exit bad; if (state == 1) exit 3; if (state == 2) exit 4 }' "$2" > "$3"
}

hr "1. README.md from the clone source, and its quickstart block"
SRCVIEW="$WORK/.readme-source"
git clone -q --no-checkout "$SOURCE" "$SRCVIEW" 2>"$LOGS/source-clone.log" || { say "RESULT: FAIL (cannot clone $SOURCE)"; cat "$LOGS/source-clone.log"; exit 2; }
if [ -n "$REF" ]; then
  SRC_SHA=$(git -C "$SRCVIEW" rev-parse --verify -q "origin/$REF^{commit}" || git -C "$SRCVIEW" rev-parse --verify -q "$REF^{commit}")
else
  SRC_SHA=$(git -C "$SRCVIEW" rev-parse --verify -q 'HEAD^{commit}')
fi
[ -n "$SRC_SHA" ] || { say "RESULT: FAIL (ref '${REF:-HEAD}' not found in $SOURCE)"; exit 2; }
git -C "$SRCVIEW" show "$SRC_SHA:README.md" > "$WORK/README.md"
say "   rehearsing $SRC_SHA ($(git -C "$SRCVIEW" log -1 --format=%s "$SRC_SHA" | cut -c1-90))"

extract_block rehearsal:linux-quickstart "$WORK/README.md" "$WORK/quickstart.readme.sh"; X=$?
ck 1 "README.md has exactly one '<!-- rehearsal:linux-quickstart' block (extract exit $X)" "$X"
FIRST=$(sed -n 1p "$WORK/quickstart.readme.sh")
URL=$(printf '%s\n' "$FIRST" | tr ' ' '\n' | grep -E '^https://' )
NURL=$(printf '%s\n' "$FIRST" | tr ' ' '\n' | grep -c -E '^https://')
ck 1 "the block's first line is a git clone with exactly one https:// URL" \
  "$(t sh -c "case \"\$1\" in 'git clone '*) [ \"\$2\" = 1 ] ;; *) false ;; esac" _ "$FIRST" "$NURL")"
CLONE_DIR=$(printf '%s\n' "$FIRST" | awk '{print $NF}')
BRANCH_ARG=""
if [ -n "$REF" ]; then BRANCH_ARG="--branch $REF "; fi
awk -v url="$URL" -v src="$SOURCE" -v br="$BRANCH_ARG" 'NR == 1 {
    i = index($0, url); $0 = substr($0, 1, i - 1) src substr($0, i + length(url))
    if (br != "") sub(/^git clone /, "git clone " br)
  } { print }' "$WORK/quickstart.readme.sh" > "$WORK/quickstart.run.sh"
say "   --- README.md's block, verbatim:"
sed 's/^/   | /' "$WORK/quickstart.readme.sh"
say "   --- diff against the block that runs:"
diff -u "$WORK/quickstart.readme.sh" "$WORK/quickstart.run.sh" | sed 's/^/   | /'
# unified format on GNU and busybox alike: count the -/+ lines, not the ---/+++ headers
NDIFF=$(diff -u "$WORK/quickstart.readme.sh" "$WORK/quickstart.run.sh" | grep -E '^[-+]' | grep -c -v -E '^(---|\+\+\+) ')
ck 1 "the block runs as written: only the clone line differs ($NDIFF changed line(s) in the diff, 2 expected)" \
  "$(t [ "$NDIFF" = 2 ])"

hr "1. the quickstart, run with sh -ex in $WORK (timeout ${TIMEOUT}s)"
QS0=$(now)
(cd "$WORK" && timeout "$TIMEOUT" sh -ex "$WORK/quickstart.run.sh") > "$LOGS/quickstart.log" 2>&1
QS_RC=$?
quiet < "$LOGS/quickstart.log" | sed 's/^/   | /'
say "   quickstart exit $QS_RC after $(( $(now) - QS0 ))s"
CLONE="$WORK/$CLONE_DIR"
ck 1 "the quickstart block exited 0 (exit $QS_RC; 124 = the timeout)" "$QS_RC"
ck 1 "the clone exists at $CLONE with the OB1 submodule checked out" \
  "$(t sh -c "[ -e '$CLONE/OB1/.git' ] && [ -n \"\$(ls -A '$CLONE/OB1')\" ]")"
[ -d "$CLONE" ] || { say "RESULT: FAIL (no clone directory - nothing more can be checked)"; exit 1; }
cd "$CLONE" || exit 1
CLONE_HEAD=$(git rev-parse HEAD)
say "   clone HEAD $CLONE_HEAD; submodule: $(git submodule status | cut -c1-60)"
ck 1 "the clone's HEAD is the rehearsed commit" "$(t [ "$CLONE_HEAD" = "$SRC_SHA" ])"
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3000/health)
ck 1 "GET http://127.0.0.1:3000/health -> $CODE" "$(t [ "$CODE" = 200 ])"
ck 1 "no NVIDIA runtime on this daemon (runtimes: $RUNTIMES)" "$(t sh -c "! printf '%s' '$RUNTIMES' | grep -qi nvidia")"
say "   containers:"
docker ps -a --format '{{.Names}}|{{.Label "com.docker.compose.project"}}|{{.Image}}|{{.Status}}' | sort | sed 's/^/     /'
NCONT=$(docker ps -aq | wc -l | tr -d ' ')
PROJECTS=$(docker ps -a --format '{{.Label "com.docker.compose.project"}}' | sort -u | tr '\n' ' ' | sed 's/ $//')
ck 1 "every container belongs to compose project 'frontend' (projects: ${PROJECTS:-none})" "$(t [ "$PROJECTS" = frontend ])"
NSVC=$(docker compose -f frontend/docker-compose.yml config --services 2>/dev/null | wc -l | tr -d ' ')
README_COUNT=$(grep -o 'stack:count:frontend:stock -->\*\*[0-9]*' README.md | grep -o '[0-9]*$')
ck 1 "$NCONT container(s) = the $NSVC service(s) frontend renders with its .env = README's count (${README_COUNT:-none})" \
  "$(t sh -c "[ '$NCONT' = '$NSVC' ] && [ '$NCONT' = '${README_COUNT:-x}' ]")"

# ---- 2. enabling products -----------------------------------------------------------
# stub <KEY>: a value that passes the driver's key check; the Mullvad pair is shaped like
# a real WireGuard key and address (the address is assembled so no literal sits here).
stub() {
  case "$1" in
    MULLVAD_WG_PRIVATE_KEY) python3 -c 'import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())' ;;
    MULLVAD_WG_ADDRESSES) printf '%s.%s.%s.%s/32\n' 10 "$(( $$ % 200 + 20 ))" 0 "$(( $$ % 150 + 50 ))" ;;
    *) python3 -c 'import secrets; print(secrets.token_hex(24))' ;;
  esac
}
# set_key <file> <KEY> <value>: replace the KEY= line, or append one
set_key() {
  python3 - "$1" "$2" "$3" <<'PY'
import re, sys
path, key, val = sys.argv[1:4]
text = open(path, encoding='utf-8').read()
new, n = re.subn(r'(?m)^' + re.escape(key) + r'=.*$', lambda m: key + '=' + val, text)
if n == 0:
    new = text + ('' if text.endswith('\n') or not text else '\n') + key + '=' + val + '\n'
open(path, 'w', encoding='utf-8', newline='\n').write(new)
PY
}
# enable_loop <product> [enable args]: the README's loop - `enable`, copy the named
# file's .env.example if the file is missing, fill in what it named, `enable` again.
# Sets ENABLE_RC, ENABLE_FIRST (the first refusal) and ENABLE_LAST (the last output).
enable_loop() {
  _p=$1; ENABLE_FIRST=""; _round=0
  while :; do
    _round=$((_round + 1))
    ENABLE_LAST=$(python3 scripts/stack/stack.py enable "$_p" 2>&1); ENABLE_RC=$?
    say "   \$ python3 scripts/stack/stack.py enable $_p   (round $_round, exit $ENABLE_RC)"
    printf '%s\n' "$ENABLE_LAST" | sed 's/^/   | /'
    [ "$ENABLE_RC" -eq 0 ] && return 0
    [ -z "$ENABLE_FIRST" ] && ENABLE_FIRST=$ENABLE_LAST
    [ "$_round" -ge 5 ] && return 1
    _named=$(printf '%s\n' "$ENABLE_LAST" | grep -E '^  [A-Z][A-Z0-9_]* is (missing in|still the placeholder)') || return 1
    printf '%s\n' "$_named" | while IFS= read -r _line; do
      _key=$(printf '%s\n' "$_line" | awk '{print $1}')
      _file=$(printf '%s\n' "$_line" | sed -e 's/ (read by .*)$//' | awk '{print $NF}')
      if [ ! -e "$_file" ]; then cp "$_file.example" "$_file" && say "   cp $_file.example $_file"; fi
      set_key "$_file" "$_key" "$(stub "$_key")" && say "   set $_key in $_file (generated)"
    done
  done
}
# up_order <log>: the planes `up` brought up, in the order it ran them
up_order() { grep -E '^docker compose -f [^ ]+ up -d' "$1" | awk '{print $4}' | sed 's|/.*||' | tr '\n' ' ' | sed 's/ $//'; }
# order_ok "<planes in order>": every plane comes after each plane it requires (manifest)
order_ok() {
  python3 - "$1" <<'PY'
import sys, tomllib
order = sys.argv[1].split()
m = tomllib.load(open('stack.manifest.toml', 'rb'))
planes = m.get('planes', {})
compose_to_plane = {}
for name, p in planes.items():
    c = p.get('compose', '')
    compose_to_plane[c.split('/')[0] if '/' in c else name] = name
seq = [compose_to_plane.get(d, d) for d in order]
bad = []
for i, pl in enumerate(seq):
    for req in planes.get(pl, {}).get('requires', []):
        if req in seq and seq.index(req) > i:
            bad.append(f'{pl} before its requirement {req}')
print('order: ' + ' -> '.join(seq) + ('  VIOLATIONS: ' + '; '.join(bad) if bad else '  (every requirement comes first)'))
sys.exit(1 if bad else 0)
PY
}
wait_health() {  # wait_health <seconds>: poll `stack.py health` until it exits 0
  _end=$(( $(now) + $1 ))
  while :; do
    python3 scripts/stack/stack.py health > "$LOGS/health.log" 2>&1; HEALTH_RC=$?
    [ "$HEALTH_RC" -eq 0 ] && return 0
    [ "$(now)" -ge "$_end" ] && return 1
    sleep 10
  done
}

hr "2a. enable inference (the GPU-less path)"
enable_loop inference
ck 2 "the first 'enable inference' refused and named the missing key(s) and the file" \
  "$(t sh -c "printf '%s' \"\$1\" | grep -qE '^  [A-Z_]+ is missing in inference/.env'" _ "$ENABLE_FIRST")"
ck 2 "'enable inference' succeeded once the named keys were supplied (exit $ENABLE_RC)" "$ENABLE_RC"
BEFORE_UP=$(docker ps -aq | sort | tr '\n' ' ')
python3 scripts/stack/stack.py up > "$LOGS/up-inference-refused.log" 2>&1; UP_RC=$?
say "   \$ python3 scripts/stack/stack.py up   (exit $UP_RC)"; quiet < "$LOGS/up-inference-refused.log" | sed 's/^/   | /'
ck 2 "'up' refused the GPU profile on a GPU-less daemon (exit $UP_RC, names no NVIDIA GPU)" \
  "$(t sh -c "[ $UP_RC -ne 0 ] && grep -q 'no NVIDIA GPU' '$LOGS/up-inference-refused.log'")"
ck 2 "the refusal started nothing (container set unchanged)" "$(t [ "$(docker ps -aq | sort | tr '\n' ' ')" = "$BEFORE_UP" ])"
STEPS=$(grep -E '^  [0-9]+\. `' "$LOGS/up-inference-refused.log" | sed -e 's/^  [0-9]*\. `//' -e 's/`.*$//')
NSTEPS=$(printf '%s\n' "$STEPS" | grep -c .)
ck 2 "the refusal printed numbered steps ($NSTEPS)" "$(t [ "$NSTEPS" -ge 1 ])"
say "   following the printed steps, as printed:"
STEP_FAIL=0
printf '%s\n' "$STEPS" > "$WORK/steps.txt"
: > "$LOGS/steps.log"
while IFS= read -r step; do
  [ -n "$step" ] || continue
  say "   \$ $step"
  sh -c "$step" >> "$LOGS/steps.log" 2>&1 < /dev/null; rc=$?
  say "     exit $rc"
  [ "$rc" -eq 0 ] || STEP_FAIL=$((STEP_FAIL + 1))
done < "$WORK/steps.txt"
quiet < "$LOGS/steps.log" | sed 's/^/   | /'
cp "$LOGS/steps.log" "$LOGS/up-inference.log"
ck 2 "every printed step exited 0 (the last one is the 'up')" "$STEP_FAIL"
ORDER=$(up_order "$LOGS/up-inference.log")
say "   up ran, in order: anchor (networks) then $ORDER"
OMSG=$(order_ok "$ORDER"); ORC=$?; say "   $OMSG"
ck 2 "'up' started the enabled planes in dependency order ($ORDER)" "$ORC"
ck 2 "inference containers exist after 'up'" "$(t sh -c "docker ps -a --format '{{.Label \"com.docker.compose.project\"}}' | grep -qx inference")"
wait_health 300; say "   \$ python3 scripts/stack/stack.py health   (exit $HEALTH_RC)"; sed 's/^/   | /' "$LOGS/health.log"
ck 2 "inference serves: health passes, 'llm-gateway liveliness' OK (exit $HEALTH_RC)" \
  "$(t sh -c "[ $HEALTH_RC -eq 0 ] && grep -q 'OK.*inference: llm-gateway liveliness' '$LOGS/health.log'")"

hr "2b. enable search, with stub Mullvad keys"
enable_loop search
ck 2 "the first 'enable search' refused and named the Mullvad key(s) and the file" \
  "$(t sh -c "printf '%s' \"\$1\" | grep -qE '^  MULLVAD_WG_[A-Z_]+ is missing in search/.env'" _ "$ENABLE_FIRST")"
ck 2 "'enable search' succeeded once the named keys were supplied (exit $ENABLE_RC)" "$ENABLE_RC"
python3 scripts/stack/stack.py up > "$LOGS/up-search.log" 2>&1; UP_RC=$?
say "   \$ python3 scripts/stack/stack.py up   (exit $UP_RC)"; quiet < "$LOGS/up-search.log" | sed 's/^/   | /'
ORDER=$(up_order "$LOGS/up-search.log")
OMSG=$(order_ok "$ORDER"); ORC=$?; say "   $OMSG"
ck 2 "'up' ran the planes in dependency order ($ORDER)" "$ORC"
ck 2 "search containers were created" "$(t sh -c "docker ps -a --format '{{.Label \"com.docker.compose.project\"}}' | grep -qx search")"
DOC_REASON='dependency failed to start: container search-vpn is unhealthy'
if [ "$UP_RC" -eq 0 ]; then
  info "search 'up' exited 0 with a stub key (the tunnel came up?) - checking health instead"
  ck 2 "search came up (not expected with a stub key, but not a failure)" 0
else
  ck 2 "'up' failed for the documented reason only: '$DOC_REASON'" \
    "$(t sh -c "grep -qF '$DOC_REASON' '$LOGS/up-search.log' && grep -qF '$DOC_REASON' search/README.md")"
  docker logs search-vpn > "$LOGS/search-vpn.log" 2>&1
  tail -5 "$LOGS/search-vpn.log" | sed 's/^/   | vpn: /'
  ck 2 "the vpn log says what search/README.md says a well-formed but unknown key gives" \
    "$(t sh -c "grep -q 'failed to pass the healthcheck' '$LOGS/search-vpn.log' && grep -q 'failed to pass the healthcheck' search/README.md")"
fi
python3 scripts/stack/stack.py health > "$LOGS/health-search.log" 2>&1; HEALTH_RC=$?
say "   \$ python3 scripts/stack/stack.py health   (exit $HEALTH_RC)"; sed 's/^/   | /' "$LOGS/health-search.log"
OTHER_FAIL=$(grep '\[FAIL\]' "$LOGS/health-search.log" | grep -c -v -E '\[FAIL\] search:|unhealthy containers \(found: search-[a-z-]*\)')
ck 2 "every other plane still passes its probes; only search's fail ($OTHER_FAIL other failure(s))" "$(t [ "$OTHER_FAIL" = 0 ])"

hr "G19 probe, before the Contributing loop (INFO; Open Brain from a fresh clone is out of scope)"
G19A=$(python3 scripts/stack/stack.py enable open-brain 2>&1); G19A_RC=$?
printf '%s\n' "$G19A" | sed 's/^/   | /'
info "G19 before the Contributing loop: enable open-brain exit $G19A_RC: $(printf '%s\n' "$G19A" | sed -n 2p | sed 's/^ *//' | cut -c1-160)"

# ---- 4. code repo contents (on the commit as cloned, before any rehearsal commit) ----
hr "4. code repo contents at $CLONE_HEAD"
JOURNAL=$(git ls-tree -r --name-only "$CLONE_HEAD" | grep -E '^documentation/(notes|evidence|archive)/|^(PLAN[^/]*|TEST-PLAN[^/]*|TEST_PLAN[^/]*|CLEANUP-PLAN\.md)$')
NTRACK=$(git ls-tree -r --name-only "$CLONE_HEAD" | wc -l | tr -d ' ')
say "   $NTRACK tracked paths; journal-shaped: ${JOURNAL:-none}"
ck 4 "no operator journal tracked (documentation/notes|evidence|archive, root PLAN*/TEST-PLAN*/CLEANUP-PLAN.md)" "$(t [ -z "$JOURNAL" ])"
DENY=""
[ -e .identity-denylist ] && DENY=.identity-denylist
[ -e "$(git rev-parse --git-common-dir)/identity-denylist" ] && DENY="$DENY git-common-dir/identity-denylist"
[ -n "${AI_STACK_IDENTITY_DENYLIST:-}" ] && DENY="$DENY \$AI_STACK_IDENTITY_DENYLIST"
ck 4 "no identity denylist exists in the clone (${DENY:-none})" "$(t [ -z "$DENY" ])"
python3 scripts/checks/check_identity.py --all > "$LOGS/identity-all.log" 2>&1; ID_RC=$?
sed 's/^/   | /' "$LOGS/identity-all.log"
ck 4 "check_identity.py --all exited 0 (exit $ID_RC)" "$ID_RC"

# ---- 5a. agent routing: what CLAUDE.md tells an agent --------------------------------
hr "5. agent routing: CLAUDE.md's plan-store section, quoted"
awk '/^## Where documentation goes/ { on = 1 } on && /^## / && !/^## Where documentation goes/ { exit } on' CLAUDE.md > "$WORK/claude-routing.md"
grep -n -E 'plan store|plans, notes|findings|evidence|journal/notes|test plan' "$WORK/claude-routing.md" | sed 's/^/   > /'
for word in plan note finding evidence documentation-plans-ai-stack; do
  ck 5 "CLAUDE.md's plan-store section names '$word'" "$(t grep -qi "$word" "$WORK/claude-routing.md")"
done
ck 5 "the section says they GO to the plan store ('go to the private plan store')" "$(t grep -qi 'go to the private plan store' "$WORK/claude-routing.md")"

# ---- 3. committing from Linux --------------------------------------------------------
hr "3. committing from Linux (git + python3; README's Contributing blocks as written)"
ck 3 "no PowerShell on PATH (pwsh, powershell.exe): the Python-only host path is what is tested" \
  "$(t sh -c '! command -v pwsh >/dev/null 2>&1 && ! command -v powershell.exe >/dev/null 2>&1')"
git config user.name "linux rehearsal"
git config user.email rehearsal@example.invalid
extract_block rehearsal:contributing-hooks README.md "$WORK/hooks.sh"; X=$?
ck 3 "README.md has exactly one '<!-- rehearsal:contributing-hooks' block (extract exit $X)" "$X"
sed 's/^/   | /' "$WORK/hooks.sh"
sh -ex "$WORK/hooks.sh" > "$LOGS/hooks-block.log" 2>&1; HB_RC=$?
sed 's/^/   | /' "$LOGS/hooks-block.log"
ck 3 "the hooks block exited 0 and printed 'hooks can run' (exit $HB_RC)" \
  "$(t sh -c "[ $HB_RC -eq 0 ] && grep -qx 'hooks can run' '$LOGS/hooks-block.log'")"

LEDGER="$(git rev-parse --git-common-dir)/hook-attest.log"
# try_commit <label> <message>: commit what is staged; sets COMMIT_RC, prints the summary
try_commit() {
  _before=$(git rev-parse HEAD)
  git commit -m "$2" > "$LOGS/commit-$1.log" 2>&1; COMMIT_RC=$?
  COMMIT_MOVED=1; [ "$(git rev-parse HEAD)" != "$_before" ] && COMMIT_MOVED=0
  say "   \$ git commit   ($1: exit $COMMIT_RC, HEAD $( [ $COMMIT_MOVED -eq 0 ] && echo moved || echo 'did not move'))"
  grep -E '^(SKIPPED|REFUSED|Pre-commit|FAIL|  \[|\[)' "$LOGS/commit-$1.log" | sed 's/^/   | /'
  SUMMARY=$(grep '^Pre-commit gates (host:' "$LOGS/commit-$1.log")
}
unstage_rm() { git rm -q --cached -- "$1" >/dev/null 2>&1; rm -f -- "$1"; }

printf '\nA line added by the Linux rehearsal.\n' >> frontend/README.md
git add frontend/README.md
try_commit doc-before-loop "docs: a doc-only commit from the Linux rehearsal"
ck 3 "a doc-only commit succeeds right after the hooks block (exit $COMMIT_RC)" "$(t sh -c "[ $COMMIT_RC = 0 ] && [ $COMMIT_MOVED = 0 ]")"
ck 3 "its summary names the gates that RAN and those SKIPPED, on host python3" \
  "$(t sh -c "printf '%s' \"\$1\" | grep -q 'Pre-commit gates (host: python3) - RAN: .* | SKIPPED: '" _ "$SUMMARY")"
TREE=$(git rev-parse 'HEAD^{tree}')
ck 3 "the attestation ledger has a line for that tree, with a skipped= column" "$(t sh -c "grep '^$TREE ' '$LEDGER' | grep -q 'skipped='")"
case "$SUMMARY" in *"SKIPPED:"*docs-blocks*) info "docs-blocks SKIPPED on the doc-only commit made before the Contributing .env loop (known carry: its OB1/docker/.env is absent)";; esac

extract_block rehearsal:contributing-checks README.md "$WORK/checks.sh"; X=$?
ck 3 "README.md has exactly one '<!-- rehearsal:contributing-checks' block (extract exit $X)" "$X"
sed 's/^/   | /' "$WORK/checks.sh"
CB0=$(now)
sh -ex "$WORK/checks.sh" > "$LOGS/checks-block.log" 2>&1; CB_RC=$?
grep -E '^\+ |passed|failed|All checks|OK\]|FAIL|error' "$LOGS/checks-block.log" | grep -v '^+ *\[' | tail -25 | sed 's/^/   | /'
say "   checks block exit $CB_RC after $(( $(now) - CB0 ))s"
ck 3 "the Contributing checks block (venv, ruff, pytest, inventory --check, docs --check) exited 0 (exit $CB_RC)" "$CB_RC"

printf '\nA second line added by the Linux rehearsal.\n' >> frontend/README.md
git add frontend/README.md
try_commit doc-after-loop "docs: a second doc-only commit, after the .env loop"
ck 3 "a doc-only commit after the Contributing loop succeeds (exit $COMMIT_RC)" "$(t sh -c "[ $COMMIT_RC = 0 ] && [ $COMMIT_MOVED = 0 ]")"
case "$SUMMARY" in *"RAN:"*docs-blocks*"| SKIPPED"*) info "docs-blocks RAN on the doc-only commit after the .env loop";; *) info "docs-blocks did not run on the doc-only commit after the .env loop: $SUMMARY";; esac

printf '\n# A comment added by the Linux rehearsal.\n' >> scripts/stack/stack.py
git add scripts/stack/stack.py
try_commit python "stack: a Python change from the Linux rehearsal"
ck 3 "a Python-change commit succeeds (exit $COMMIT_RC)" "$(t sh -c "[ $COMMIT_RC = 0 ] && [ $COMMIT_MOVED = 0 ]")"
ck 3 "its summary names the gates that RAN and those SKIPPED" \
  "$(t sh -c "printf '%s' \"\$1\" | grep -q 'RAN: .* | SKIPPED: '" _ "$SUMMARY")"

# planted violations, each assembled at run time so no such literal sits in this file
KEY="gw-$(python3 -c 'import secrets; print(secrets.token_hex(20))')"
printf 'GATEWAY_KEY=%s\n' "$KEY" > rehearsal-planted-key.txt
git add rehearsal-planted-key.txt
try_commit secret "planted: must be refused"
# Each refusal must come from THE gate that owns it: a commit refused by some earlier gate
# also leaves HEAD unmoved, and would read as a pass.
ck 3 "a planted gateway key is refused by the secret guard (exit $COMMIT_RC, HEAD unmoved), its text not echoed" \
  "$(t sh -c "[ $COMMIT_RC != 0 ] && [ $COMMIT_MOVED = 1 ] && grep -qi 'gateway key' '$LOGS/commit-secret.log' && ! grep -qF '$KEY' '$LOGS/commit-secret.log'")"
unstage_rm rehearsal-planted-key.txt

printf '#!/bin/sh\r\necho planted\r\n' > scripts/rehearsal-planted-crlf.sh
git add scripts/rehearsal-planted-crlf.sh
try_commit crlf "planted: must be refused"
ck 3 "a shell script with CRLF line endings is refused (exit $COMMIT_RC, HEAD unmoved)" \
  "$(t sh -c "[ $COMMIT_RC != 0 ] && [ $COMMIT_MOVED = 1 ] && grep -q 'rehearsal-planted-crlf.sh' '$LOGS/commit-crlf.log' && grep -q 'Line ending validation failed' '$LOGS/commit-crlf.log'")"
unstage_rm scripts/rehearsal-planted-crlf.sh

IP="192.168.$(( $$ % 200 + 20 )).$(( $$ % 150 + 50 ))"
printf 'the upstream host is %s\n' "$IP" > rehearsal-planted-identity.md
git add rehearsal-planted-identity.md
try_commit identity "planted: must be refused"
ck 3 "a planted private LAN address is refused by the identity gate (exit $COMMIT_RC, HEAD unmoved)" \
  "$(t sh -c "[ $COMMIT_RC != 0 ] && [ $COMMIT_MOVED = 1 ] && grep -q 'rehearsal-planted-identity.md:1:[0-9]*: lan-ip' '$LOGS/commit-identity.log'")"
unstage_rm rehearsal-planted-identity.md

hr "5b. a note staged into the code repo"
mkdir -p documentation/notes
printf '# a finding that belongs in the plan store\n' > documentation/notes/rehearsal-note.md
git add documentation/notes/rehearsal-note.md
try_commit notes "notes: must be refused"
ck 5 "staging documentation/notes/rehearsal-note.md is refused by the doc-placement gate (exit $COMMIT_RC, HEAD unmoved)" \
  "$(t sh -c "[ $COMMIT_RC != 0 ] && [ $COMMIT_MOVED = 1 ] && grep -q 'planning or journal material staged' '$LOGS/commit-notes.log'")"
ck 5 "the refusal names the plan-store path to write it at" \
  "$(t grep -q 'documentation-plans-ai-stack/journal/notes/rehearsal-note.md' "$LOGS/commit-notes.log")"
unstage_rm documentation/notes/rehearsal-note.md

hr "G19 probe, after the Contributing loop (INFO)"
if enable_loop open-brain; then
  docker compose -f OB1/docker/docker-compose.yml config -q > "$LOGS/ob1-render.log" 2>&1; R=$?
  info "G19 after the Contributing loop: enable open-brain succeeded; OB1 render exit $R$( [ $R -ne 0 ] && printf ': %s' "$(head -1 "$LOGS/ob1-render.log" | cut -c1-160)")"
  python3 scripts/stack/stack.py disable open-brain > /dev/null 2>&1
else
  info "G19 after the Contributing loop: enable open-brain still refused: $(printf '%s\n' "$ENABLE_LAST" | sed -n 2p | sed 's/^ *//' | cut -c1-200)"
fi

# ---- teardown (only what this run could have made: the daemon was empty) -------------
hr "teardown"
python3 scripts/stack/stack.py down > "$LOGS/down.log" 2>&1
# word lists of the ids and names docker printed, split on purpose:
IDS=$(docker ps -aq)
VOLS=$(docker volume ls -q)
NETS=$(docker network ls --format '{{.Name}}' | grep -vxE 'bridge|host|none')
# shellcheck disable=SC2086
[ -n "$IDS" ] && docker rm -f -v $IDS > /dev/null 2>&1
# shellcheck disable=SC2086
[ -n "$VOLS" ] && docker volume rm -f $VOLS > /dev/null 2>&1
# shellcheck disable=SC2086
[ -n "$NETS" ] && docker network rm $NETS > /dev/null 2>&1
say "   removed every container, volume and non-default network on this daemon"

# ---- summary -------------------------------------------------------------------------
hr "SUMMARY"
title() {
  case "$1" in
    1) echo "fresh clone, frontend only" ;;
    2) echo "enabling a product (inference GPU-less, search with stub keys)" ;;
    3) echo "committing from Linux (git + python3)" ;;
    4) echo "code repo contents" ;;
    5) echo "agent routing" ;;
  esac
}
ALLFAIL=0
for n in 1 2 3 4 5; do
  f=0; r=0
  eval "f=\$C${n}_FAIL; r=\$C${n}_RAN"
  title=$(title "$n")
  if [ "$f" -eq 0 ] && [ "$r" -gt 0 ]; then
    say "CRITERION $n: PASS - $title ($r check(s))"
  else
    say "CRITERION $n: FAIL - $title ($f of $r check(s) failed)"; ALLFAIL=$((ALLFAIL + 1))
  fi
done
say "CRITERION 6: NOT RUN HERE - host-side render comparison (see the test plan)"
printf '%s\n' "$INFO" | grep -v '^$'
say "RUNTIME: $(( $(now) - T0 ))s inside (quickstart ${QS_RC:+exit $QS_RC})"
if [ "$ALLFAIL" -eq 0 ]; then say "RESULT: PASS"; exit 0; fi
say "RESULT: FAIL ($ALLFAIL criterion/criteria)"
exit 1
