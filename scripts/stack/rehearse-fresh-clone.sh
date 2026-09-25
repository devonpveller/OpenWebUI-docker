#!/usr/bin/env bash
# rehearse-fresh-clone.sh - a fresh clone, a REAL `stack.py up`, and /health,
# inside an isolated Docker-in-Docker daemon. Never the host daemon.
#
# WHY A DinD: every plane uses fixed container names (`openwebui`, ...), so a
# second checkout driven against the host daemon would drive the host's own
# deployment. Here the clone, the daemon and every container it starts live
# inside ONE privileged `docker:*-dind` container, which is removed at the end
# (with its anonymous /var/lib/docker volume) on every exit path the shell can
# trap. The host daemon sees exactly one container and nothing else: no port is
# published, no host path is mounted, the host's docker.sock is never shared.
#
# WHAT IT DOES, in order:
#   1. starts the DinD container (labelled ai-stack.harness.owner=<owner>, so
#      scripts/agent-harness/reap.ps1 can clean it up if this script is killed)
#   2. installs python3, git, curl, openssl inside it
#   3. clones --ref from THIS repository into /work/<dir-name> inside it, via a
#      git bundle (no network, no push anywhere; the OB1 submodule is NOT
#      initialised - a frontend-only clone does not need it)
#   4. copies frontend/.env.example to frontend/.env and replaces the shipped
#      WEBUI_SECRET_KEY placeholder with a generated one (unless --placeholder)
#   5. runs `python3 scripts/stack/stack.py init`, then a REAL
#      `python3 scripts/stack/stack.py up`
#   6. polls http://localhost:3000/health INSIDE the DinD until 200
#   7. checks: only frontend-plane containers exist; ai-stack_llm-net,
#      ai-stack_app-net and ai-stack_default exist, llm-net internal
#   8. runs `stack.py health` and reports its exit code
#   then tears the DinD down.
#
# --this-daemon runs the same clone, init, REAL up, /health and checks against the
# daemon `docker` already talks to, with no DinD - for a CI runner, whose daemon is
# itself disposable (the `fresh-clone-up` job in .github/workflows/ci.yml). It
# REFUSES to start when that daemon holds any container at all, so pointed at a
# host that runs a stack it stops before touching anything. The clone goes in a
# temp directory; step 9 then tears down what the run created (`stack.py down`,
# then every container, and each volume and network that did not exist before
# the run) and checks that nothing is left. --preload-image does not apply.
#
# --placeholder leaves the shipped WEBUI_SECRET_KEY in frontend/.env and checks
# the opposite: `doctor` and `up` exit non-zero naming frontend/.env and
# WEBUI_SECRET_KEY, and no container exists afterwards.
#
# Exit code: 0 when every check passed, 1 when any failed, 2 on a usage error.
# The last line is always `RESULT: PASS` or `RESULT: FAIL (<n> check(s))`.
#
# Usage (from anywhere inside the repository, Linux or Git Bash):
#   scripts/stack/rehearse-fresh-clone.sh [--ref <commit-ish>] [--dir-name <dir>]
#       [--name <container>] [--owner <id>] [--image <dind image>]
#       [--preload-image <image>] [--health-timeout <seconds>] [--placeholder] [--keep]
#       [--this-daemon]
#
#   --ref            what to clone (default HEAD). Base reproduction: --ref 3c3ff75
#   --dir-name       the clone directory inside the DinD (default ai-stack). Use
#                    e.g. openwebui-docker to prove network names do not follow it
#   --name           the DinD container name (default rehearse-fresh-clone-<pid>)
#   --owner          the ai-stack.harness.owner label (default rehearse-fresh-clone)
#   --image          the DinD image (default docker:27-dind)
#   --preload-image  `docker save` this image on the host and load it into the
#                    DinD, instead of letting compose pull it (saves a multi-GB
#                    download when the host already has it; changes nothing else)
#   --health-timeout seconds to wait for /health (default 600)
#   --keep           leave the DinD container running for inspection (with
#                    --this-daemon: leave the containers the run started)
#   --this-daemon    no DinD: drive the current daemon, which must hold no
#                    container (see above)

set -uo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

REF=HEAD
DIR_NAME=ai-stack
NAME="rehearse-fresh-clone-$$"
OWNER=rehearse-fresh-clone
IMAGE=docker:27-dind
PRELOAD=""
HEALTH_TIMEOUT=600
PLACEHOLDER=0
KEEP=0
THIS_DAEMON=0

usage() { sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
    --dir-name) DIR_NAME="${2:?--dir-name needs a value}"; shift 2 ;;
    --name) NAME="${2:?--name needs a value}"; shift 2 ;;
    --owner) OWNER="${2:?--owner needs a value}"; shift 2 ;;
    --image) IMAGE="${2:?--image needs a value}"; shift 2 ;;
    --preload-image) PRELOAD="${2:?--preload-image needs a value}"; shift 2 ;;
    --health-timeout) HEALTH_TIMEOUT="${2:?--health-timeout needs a value}"; shift 2 ;;
    --placeholder) PLACEHOLDER=1; shift ;;
    --keep) KEEP=1; shift ;;
    --this-daemon) THIS_DAEMON=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)"; exit 2 ;;
  esac
done

case "$DIR_NAME" in
  ""|*/*|.|..) echo "--dir-name must be a single directory name, got '$DIR_NAME'"; exit 2 ;;
esac

REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "not inside a git checkout: $(dirname "$0")"; exit 2; }
SHA="$(git -C "$REPO" rev-parse --verify "${REF}^{commit}" 2>/dev/null)" || {
  echo "--ref '$REF' does not name a commit in $REPO"; exit 2; }

if [ "$THIS_DAEMON" -eq 1 ]; then
  [ -n "$PRELOAD" ] && { echo "--preload-image has no meaning with --this-daemon (there is no second daemon to load into)"; exit 2; }
  # THE GUARD THIS MODE STANDS ON. Every plane uses fixed container names, so on a
  # daemon that runs a stack, `stack.py up` here would drive THAT stack. A daemon
  # that answers and holds zero containers cannot be running one.
  EXISTING="$(docker ps -aq 2>/dev/null)" || { echo "refused: --this-daemon, but 'docker ps -a' failed - no daemon to drive"; exit 2; }
  if [ -n "$EXISTING" ]; then
    echo "refused: --this-daemon drives the daemon docker talks to, and it already holds $(echo "$EXISTING" | wc -l | tr -d ' ') container(s)."
    echo "  This mode is for a disposable daemon with nothing on it (a CI runner). Run without --this-daemon to rehearse inside a DinD."
    exit 2
  fi
  VOLS_BEFORE="$(docker volume ls -q | sort)"
  NETS_BEFORE="$(docker network ls -q | sort)"
elif docker container inspect "$NAME" >/dev/null 2>&1; then
  echo "refused: a container named $NAME already exists on this daemon; pick another --name"
  exit 2
fi

TMP="$(mktemp -d)"
# Under Git Bash, git.exe and docker.exe are Windows binaries and MSYS path
# conversion is OFF (above, so container paths like /work reach docker intact).
# Hand them the mixed form C:/... that both - and bash - understand.
if command -v cygpath >/dev/null 2>&1; then TMP="$(cygpath -m "$TMP")"; fi
FAILS=0
check() {  # check <description> <0|nonzero>
  if [ "$2" -eq 0 ]; then echo "  [OK]   $1"; else echo "  [FAIL] $1"; FAILS=$((FAILS + 1)); fi
}
if [ "$THIS_DAEMON" -eq 1 ]; then
  WORKROOT="$TMP/work"
  dx() { "$@"; }                                           # run here, on this daemon
  dw() { (cd "$WORKROOT/$DIR_NAME" && "$@"); }             # ... in the clone
else
  WORKROOT=/work
  dx() { docker exec "$NAME" "$@"; }                       # run inside the DinD
  dw() { docker exec -w "/work/$DIR_NAME" "$NAME" "$@"; }  # ... in the clone
fi

# --this-daemon only: remove what this run created. The daemon held no container
# when the run started (the guard above), so every container now on it is ours;
# volumes and networks are compared against the lists taken at the start.
teardown_this_daemon() {
  if [ -d "$WORKROOT/$DIR_NAME" ]; then
    (cd "$WORKROOT/$DIR_NAME" && python3 scripts/stack/stack.py down) >/dev/null 2>&1
  fi
  local ids v n
  ids="$(docker ps -aq)"; [ -n "$ids" ] && docker rm -f -v $ids >/dev/null 2>&1
  for v in $(docker volume ls -q); do
    echo "$VOLS_BEFORE" | grep -qxF "$v" || docker volume rm -f "$v" >/dev/null 2>&1
  done
  for n in $(docker network ls -q); do
    echo "$NETS_BEFORE" | grep -qxF "$n" || docker network rm "$n" >/dev/null 2>&1
  done
}

cleanup() {
  if [ "$THIS_DAEMON" -eq 1 ]; then
    if [ "$KEEP" -eq 1 ]; then
      echo "# --keep: the containers this run started are left on this daemon"
    elif [ "${TORN_DOWN:-0}" -eq 0 ]; then
      teardown_this_daemon; echo "# tore down what this run created"
    fi
    rm -rf "$TMP"
    return
  fi
  rm -rf "$TMP"
  if [ "$KEEP" -eq 1 ]; then
    echo "# --keep: $NAME left running (docker rm -f -v $NAME when done)"
  else
    docker rm -f -v "$NAME" >/dev/null 2>&1 && echo "# removed $NAME and its volume"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if [ "$THIS_DAEMON" -eq 1 ]; then
  echo "== rehearsal: ref $REF ($SHA), clone dir $WORKROOT/$DIR_NAME, THIS daemon (no DinD; it held no container)"
  # 1+2. no DinD to start or provision: the tools must already be here
  for tool in python3 git curl openssl; do
    command -v "$tool" >/dev/null 2>&1 || { echo "RESULT: FAIL ($tool is not on PATH)"; exit 1; }
  done
  echo "   daemon: docker $(docker version --format '{{.Server.Version}}'), $(docker compose version)"
  echo "   $(python3 --version)"
  # 3. the clone. No bundle: nothing crosses a container boundary here, and a CI
  #    checkout is shallow (a shallow repository cannot push into the bundle source,
  #    but it can be cloned from).
  mkdir -p "$WORKROOT"
  git clone -q --no-local --no-checkout "$REPO" "$WORKROOT/$DIR_NAME" || { echo "RESULT: FAIL (clone)"; exit 1; }
  git -C "$WORKROOT/$DIR_NAME" checkout -q -B rehearse "$SHA" || { echo "RESULT: FAIL (checkout $SHA)"; exit 1; }
  echo "   cloned $(dw git log -1 --format='%h %s' | cut -c1-100)"
else
echo "== rehearsal: ref $REF ($SHA), clone dir /work/$DIR_NAME, DinD $NAME ($IMAGE)"

# 1. the isolated daemon
docker run --privileged -d --name "$NAME" --label "ai-stack.harness.owner=$OWNER" \
  -e DOCKER_TLS_CERTDIR= "$IMAGE" >/dev/null || { echo "RESULT: FAIL (could not start $IMAGE)"; exit 1; }
for _ in $(seq 1 60); do dx docker info >/dev/null 2>&1 && break; sleep 2; done
dx docker info >/dev/null 2>&1 || { echo "RESULT: FAIL (the DinD daemon never answered)"; exit 1; }
echo "   daemon: docker $(dx docker version --format '{{.Server.Version}}'), $(dx docker compose version)"

# 2. tools a Linux newcomer needs: Python and git (docker is the DinD itself)
dx apk add --no-cache -q python3 git curl openssl >/dev/null || {
  echo "RESULT: FAIL (apk add python3 git curl openssl failed inside the DinD)"; exit 1; }
echo "   $(dx python3 --version)"

# 3. the clone, via a bundle made from a throwaway bare repo (a bundle cannot be
#    cut from a bare sha, and pushing into a temp repo leaves $REPO's refs alone)
git init -q --bare "$TMP/src.git"
git -C "$REPO" push -q "$TMP/src.git" "$SHA:refs/heads/rehearse" || { echo "RESULT: FAIL (bundle source)"; exit 1; }
git -C "$TMP/src.git" bundle create "$TMP/clone.bundle" rehearse 2>/dev/null || { echo "RESULT: FAIL (bundle)"; exit 1; }
# Streamed, not `docker cp`: docker cp reads a Windows `C:/...` source as
# container `C`, path `/...`.
docker exec -i "$NAME" sh -c 'cat > /tmp/clone.bundle' < "$TMP/clone.bundle" || {
  echo "RESULT: FAIL (copying the bundle into the DinD)"; exit 1; }
dx mkdir -p /work
dx git clone -q --branch rehearse /tmp/clone.bundle "/work/$DIR_NAME" || { echo "RESULT: FAIL (clone)"; exit 1; }
echo "   cloned $(dw git log -1 --format='%h %s' | cut -c1-100)"
fi

if [ -n "$PRELOAD" ]; then
  echo "   preloading $PRELOAD from the host daemon"
  docker save "$PRELOAD" | docker exec -i "$NAME" docker load -q || echo "   (preload failed; compose will pull)"
fi

# 4. the one file a newcomer must write
dw cp frontend/.env.example frontend/.env
if [ "$PLACEHOLDER" -eq 0 ]; then
  dw sh -c 'k=$(openssl rand -hex 32) && sed -i "s/^WEBUI_SECRET_KEY=.*/WEBUI_SECRET_KEY=$k/" frontend/.env'
  echo "   frontend/.env from .env.example, WEBUI_SECRET_KEY generated"
else
  echo "   frontend/.env from .env.example, WEBUI_SECRET_KEY LEFT AT THE SHIPPED PLACEHOLDER"
fi

echo ""
echo "== python3 scripts/stack/stack.py init"
dw python3 scripts/stack/stack.py init
INIT=$?

if [ "$PLACEHOLDER" -eq 1 ]; then
  echo ""
  echo "== python3 scripts/stack/stack.py doctor"
  DOCTOR_OUT="$(dw python3 scripts/stack/stack.py doctor)"; DOCTOR=$?
  echo "$DOCTOR_OUT"
  echo ""
  echo "== python3 scripts/stack/stack.py up"
  UP_OUT="$(dw python3 scripts/stack/stack.py up)"; UP=$?
  echo "$UP_OUT"
  echo ""
  echo "== checks"
  check "doctor exited non-zero (exit $DOCTOR)" "$([ "$DOCTOR" -ne 0 ]; echo $?)"
  check "doctor names frontend/.env and WEBUI_SECRET_KEY" \
    "$(echo "$DOCTOR_OUT" | grep 'WEBUI_SECRET_KEY' | grep -q 'frontend/.env'; echo $?)"
  check "up exited non-zero (exit $UP)" "$([ "$UP" -ne 0 ]; echo $?)"
  check "up names frontend/.env and WEBUI_SECRET_KEY" \
    "$(echo "$UP_OUT" | grep 'WEBUI_SECRET_KEY' | grep -q 'frontend/.env'; echo $?)"
  RUNNING="$(dx docker ps -a --format '{{.Names}}' | tr '\n' ' ')"
  check "no container exists after the refusal (found: ${RUNNING:-none})" "$([ -z "$RUNNING" ]; echo $?)"
else
  echo ""
  echo "== python3 scripts/stack/stack.py up   (REAL, not --dry-run)"
  dw python3 scripts/stack/stack.py up
  UP=$?
  echo "   exit $UP"

  echo ""
  echo "== waiting up to ${HEALTH_TIMEOUT}s for http://localhost:3000/health inside the DinD"
  CODE=000
  START=$(date +%s)
  while :; do
    CODE="$(dx curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:3000/health 2>/dev/null)"
    [ "$CODE" = "200" ] && break
    [ $(( $(date +%s) - START )) -ge "$HEALTH_TIMEOUT" ] && break
    sleep 5
  done
  echo "   /health -> HTTP $CODE after $(( $(date +%s) - START ))s"

  echo ""
  echo "== docker ps -a (inside the DinD)"
  dx docker ps -a --format '{{.Names}}  {{.Status}}  project={{.Label "com.docker.compose.project"}}'
  echo ""
  echo "== anchor networks (inside the DinD)"
  dx docker network ls --filter name=_ --format '{{.Name}}  internal={{.Internal}}  project={{.Label "com.docker.compose.project"}}'

  echo ""
  echo "== python3 scripts/stack/stack.py health"
  dw python3 scripts/stack/stack.py health
  HEALTH=$?

  echo ""
  echo "== checks"
  check "stack.py init exited 0 (exit $INIT)" "$INIT"
  check "stack.py up exited 0 (exit $UP)" "$UP"
  check "GET /health on :3000 returned 200 (got $CODE)" "$([ "$CODE" = "200" ]; echo $?)"
  PROJECTS="$(dx docker ps -a --format '{{.Label "com.docker.compose.project"}}' | sort -u | tr '\n' ' ')"
  check "every container belongs to compose project 'frontend' (projects: ${PROJECTS:-none})" \
    "$([ "$(echo "$PROJECTS" | xargs)" = "frontend" ]; echo $?)"
  for net in ai-stack_llm-net ai-stack_app-net ai-stack_default; do
    check "network $net exists" "$(dx docker network inspect "$net" >/dev/null 2>&1; echo $?)"
  done
  check "ai-stack_llm-net is internal" \
    "$([ "$(dx docker network inspect ai-stack_llm-net --format '{{.Internal}}' 2>/dev/null)" = "true" ]; echo $?)"
  if [ "$DIR_NAME" != "ai-stack" ]; then
    STRAY="$(dx docker network ls --format '{{.Name}}' | grep -E "^${DIR_NAME}_" | tr '\n' ' ')"
    check "no network is named after the clone directory '${DIR_NAME}_*' (found: ${STRAY:-none})" \
      "$([ -z "$STRAY" ]; echo $?)"
  fi
  check "stack.py health exited 0 (exit $HEALTH)" "$HEALTH"
fi

# 9. --this-daemon: tear down, and check that nothing the run created is left
if [ "$THIS_DAEMON" -eq 1 ] && [ "$KEEP" -eq 0 ]; then
  echo ""
  echo "== teardown (this daemon)"
  teardown_this_daemon
  TORN_DOWN=1
  LEFT_C="$(docker ps -aq | wc -l | tr -d ' ')"
  LEFT_V="$(docker volume ls -q | sort | comm -13 <(echo "$VOLS_BEFORE") - | wc -l | tr -d ' ')"
  LEFT_N="$(docker network ls -q | sort | comm -13 <(echo "$NETS_BEFORE") - | wc -l | tr -d ' ')"
  check "no container is left on the daemon ($LEFT_C found)" "$([ "$LEFT_C" = 0 ]; echo $?)"
  check "no volume the run created is left ($LEFT_V found)" "$([ "$LEFT_V" = 0 ]; echo $?)"
  check "no network the run created is left ($LEFT_N found)" "$([ "$LEFT_N" = 0 ]; echo $?)"
fi

echo ""
if [ "$FAILS" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
fi
echo "RESULT: FAIL ($FAILS check(s))"
exit 1
