#!/usr/bin/env bash
# rehearse-ops.sh - backup, lose a volume, restore it; recover; stats - all with
# `python3 scripts/stack/stack.py` inside an isolated Docker-in-Docker daemon.
# Never the host daemon. The sibling of rehearse-fresh-clone.sh (same DinD
# mechanics, same isolation: one privileged `docker:*-dind` container, no port
# published, no host path mounted, the host's docker.sock never shared, removed
# with its anonymous volume on every exit path the shell can trap).
#
# WHAT IT DOES, in order (each [OK]/[FAIL] line is one check):
#   1. starts the DinD (label ai-stack.harness.owner=<owner>, so
#      scripts/agent-harness/reap.ps1 can clean it up if this is killed),
#      installs python3 git curl openssl, clones --ref into /work/ai-stack via
#      a git bundle, writes frontend/.env with a generated WEBUI_SECRET_KEY
#   2. `stack.py init` + a REAL `stack.py up` (the stock frontend), /health 200
#   3. writes a random MARKER into the Open WebUI data volume
#      (/app/backend/data/ops-marker.txt, i.e. frontend_openwebui-data)
#   4. `stack.py backup frontend`: exit 0, manifest.json lists the volume,
#      `sha256sum -c SHA256SUMS` passes
#   5. `stack.py restore` WHILE openwebui runs: refused, names the container,
#      and the volume is unchanged (same CreatedAt, marker still there)
#   6. `stack.py down frontend`, then the volume is DESTROYED (docker volume rm)
#   7. a restore from a TAMPERED copy of the backup (one byte appended to the
#      archive): refused on sha256, and the volume is still absent afterwards
#   8. `stack.py restore frontend --from <backup>`: exit 0, the volume exists
#      again with compose's project/volume labels, and holds MARKER
#   9. `stack.py up`: /health 200 and openwebui itself reads MARKER back
#  10. `stack.py stats`: real numbers for the frontend containers, and it says
#      the inference plane is not enabled
#  11. `stack.py recover frontend`: exit 0; stops openwebui-backup before
#      openwebui, starts openwebui before openwebui-backup, every container
#      passes its gate; /health 200 afterwards
#  12. PLANTED FAILURE: a healthcheck that always fails is added to
#      openwebui-backup in the clone's compose file; `stack.py recover frontend`
#      exits 1 with a refusal naming frontend, openwebui-backup and "unhealthy"
#   then tears the DinD down.
#
# Exit code: 0 when every check passed, 1 when any failed, 2 on a usage error.
# The last line is always `RESULT: PASS` or `RESULT: FAIL (<n> check(s))`.
#
# Usage (from anywhere inside the repository, Linux or Git Bash):
#   scripts/stack/rehearse-ops.sh [--ref <commit-ish>] [--name <container>]
#       [--owner <id>] [--image <dind image>] [--preload-image <image>]
#       [--health-timeout <seconds>] [--keep]
#
#   --ref            what to clone (default HEAD). Base reproduction: --ref 48dcb03
#                    (there, every step from 4 on fails: the verbs do not exist)
#   --name           the DinD container name (default ac-ops-rehearse-<pid>)
#   --owner          the ai-stack.harness.owner label (default ac-ops-portable)
#   --image          the DinD image (default docker:27-dind)
#   --preload-image  `docker save` this image on the host and load it into the
#                    DinD instead of letting compose pull it (Open WebUI's
#                    ghcr.io/open-webui/open-webui:v0.11.0 is ~5 GB)
#   --health-timeout seconds to wait for /health each time (default 600)
#   --keep           leave the DinD container running for inspection

set -uo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

REF=HEAD
NAME="ac-ops-rehearse-$$"
OWNER=ac-ops-portable
IMAGE=docker:27-dind
PRELOAD=""
HEALTH_TIMEOUT=600
KEEP=0
VOL=frontend_openwebui-data

usage() { sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
    --name) NAME="${2:?--name needs a value}"; shift 2 ;;
    --owner) OWNER="${2:?--owner needs a value}"; shift 2 ;;
    --image) IMAGE="${2:?--image needs a value}"; shift 2 ;;
    --preload-image) PRELOAD="${2:?--preload-image needs a value}"; shift 2 ;;
    --health-timeout) HEALTH_TIMEOUT="${2:?--health-timeout needs a value}"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)"; exit 2 ;;
  esac
done

REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "not inside a git checkout: $(dirname "$0")"; exit 2; }
SHA="$(git -C "$REPO" rev-parse --verify "${REF}^{commit}" 2>/dev/null)" || {
  echo "--ref '$REF' does not name a commit in $REPO"; exit 2; }

if docker container inspect "$NAME" >/dev/null 2>&1; then
  echo "refused: a container named $NAME already exists on this daemon; pick another --name"
  exit 2
fi

TMP="$(mktemp -d)"
# Under Git Bash, git.exe and docker.exe are Windows binaries and MSYS path
# conversion is OFF (above). Hand them the mixed form C:/... both understand.
if command -v cygpath >/dev/null 2>&1; then TMP="$(cygpath -m "$TMP")"; fi
FAILS=0
check() {  # check <description> <0|nonzero>
  if [ "$2" -eq 0 ]; then echo "  [OK]   $1"; else echo "  [FAIL] $1"; FAILS=$((FAILS + 1)); fi
}
dx() { docker exec "$NAME" "$@"; }                       # run inside the DinD
dw() { docker exec -w /work/ai-stack "$NAME" "$@"; }     # ... in the clone
sp() { dw python3 scripts/stack/stack.py "$@"; }         # the driver, in the clone

cleanup() {
  rm -rf "$TMP"
  if [ "$KEEP" -eq 1 ]; then
    echo "# --keep: $NAME left running (docker rm -f -v $NAME when done)"
  else
    docker rm -f -v "$NAME" >/dev/null 2>&1 && echo "# removed $NAME and its volume"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

wait_health() {  # prints the final HTTP code
  local code=000 start
  start=$(date +%s)
  while :; do
    code="$(dx curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:3000/health 2>/dev/null)"
    [ "$code" = "200" ] && break
    [ $(( $(date +%s) - start )) -ge "$HEALTH_TIMEOUT" ] && break
    sleep 5
  done
  echo "$code"
}

echo "== rehearsal: ref $REF ($SHA), DinD $NAME ($IMAGE)"

# 1. the isolated daemon, the tools, the clone, frontend/.env
docker run --privileged -d --name "$NAME" --label "ai-stack.harness.owner=$OWNER" \
  -e DOCKER_TLS_CERTDIR= "$IMAGE" >/dev/null || { echo "RESULT: FAIL (could not start $IMAGE)"; exit 1; }
for _ in $(seq 1 60); do dx docker info >/dev/null 2>&1 && break; sleep 2; done
dx docker info >/dev/null 2>&1 || { echo "RESULT: FAIL (the DinD daemon never answered)"; exit 1; }
echo "   daemon: docker $(dx docker version --format '{{.Server.Version}}'), $(dx docker compose version)"
dx apk add --no-cache -q python3 git curl openssl >/dev/null || {
  echo "RESULT: FAIL (apk add python3 git curl openssl failed inside the DinD)"; exit 1; }
echo "   $(dx python3 --version)"
git init -q --bare "$TMP/src.git"
git -C "$REPO" push -q "$TMP/src.git" "$SHA:refs/heads/rehearse" || { echo "RESULT: FAIL (bundle source)"; exit 1; }
git -C "$TMP/src.git" bundle create "$TMP/clone.bundle" rehearse 2>/dev/null || { echo "RESULT: FAIL (bundle)"; exit 1; }
docker exec -i "$NAME" sh -c 'cat > /tmp/clone.bundle' < "$TMP/clone.bundle" || {
  echo "RESULT: FAIL (copying the bundle into the DinD)"; exit 1; }
dx mkdir -p /work
dx git clone -q --branch rehearse /tmp/clone.bundle /work/ai-stack || { echo "RESULT: FAIL (clone)"; exit 1; }
echo "   cloned $(dw git log -1 --format='%h %s' | cut -c1-100)"
if [ -n "$PRELOAD" ]; then
  echo "   preloading $PRELOAD from the host daemon"
  docker save "$PRELOAD" | docker exec -i "$NAME" docker load -q || echo "   (preload failed; compose will pull)"
fi
dw cp frontend/.env.example frontend/.env
dw sh -c 'k=$(openssl rand -hex 32) && sed -i "s/^WEBUI_SECRET_KEY=.*/WEBUI_SECRET_KEY=$k/" frontend/.env'

# 2. a real bring-up of the stock frontend
echo ""
echo "== 2. stack.py init + up (REAL)"
sp init >/dev/null; INIT=$?
sp up; UP=$?
CODE="$(wait_health)"
check "init and up exited 0 (init $INIT, up $UP)" "$([ "$INIT" -eq 0 ] && [ "$UP" -eq 0 ]; echo $?)"
check "GET /health returned 200 after up (got $CODE)" "$([ "$CODE" = "200" ]; echo $?)"

# 3. the marker
MARKER="ops-marker-$(dx openssl rand -hex 16)"
dx docker exec openwebui sh -c "printf '%s' '$MARKER' > /app/backend/data/ops-marker.txt"
check "marker written into $VOL" "$(dx docker exec openwebui cat /app/backend/data/ops-marker.txt | grep -qx "$MARKER"; echo $?)"

# 4. backup
echo ""
echo "== 4. stack.py backup frontend"
sp backup frontend; BK=$?
BDIR="$(dw sh -c 'ls -d backups/frontend/manual-* 2>/dev/null | tail -n 1')"
echo "   backup directory: ${BDIR:-none}"
check "backup exited 0 (exit $BK)" "$BK"
check "manifest.json lists $VOL" "$(dw grep -q "\"volume\": \"$VOL\"" "$BDIR/manifest.json"; echo $?)"
check "sha256sum -c SHA256SUMS passes" "$(dw sh -c "cd '$BDIR' && sha256sum -c SHA256SUMS"; echo $?)"

# 5. restore while running
echo ""
echo "== 5. stack.py restore while openwebui runs (must refuse)"
CREATED_BEFORE="$(dx docker volume inspect -f '{{.CreatedAt}}' "$VOL")"
OUT="$(sp restore frontend --from "$BDIR")"; RC=$?
echo "$OUT"
check "restore while running exited non-zero (exit $RC)" "$([ "$RC" -ne 0 ]; echo $?)"
check "the refusal names the running openwebui container" "$(echo "$OUT" | grep 'in use by' | grep -q openwebui; echo $?)"
check "the volume was not recreated (CreatedAt unchanged)" \
  "$([ "$(dx docker volume inspect -f '{{.CreatedAt}}' "$VOL")" = "$CREATED_BEFORE" ]; echo $?)"
check "the marker is still there and nothing was staged" \
  "$(dx docker exec openwebui sh -c "grep -qx '$MARKER' /app/backend/data/ops-marker.txt && ! test -e /app/backend/data/.stack-restore-staging"; echo $?)"

# 6. lose the volume
echo ""
echo "== 6. stack.py down frontend, then destroy $VOL"
sp down frontend >/dev/null; DOWN=$?
dx docker volume rm "$VOL" >/dev/null; RM=$?
check "down exited 0 and the volume was removed (down $DOWN, rm $RM)" "$([ "$DOWN" -eq 0 ] && [ "$RM" -eq 0 ]; echo $?)"

# 7. a tampered archive
echo ""
echo "== 7. restore from a TAMPERED copy (must refuse on sha256 and change nothing)"
dw sh -c "rm -rf /tmp/tampered && cp -r '$BDIR' /tmp/tampered && printf x >> /tmp/tampered/$VOL.tar.gz"
OUT="$(sp restore frontend --from /tmp/tampered)"; RC=$?
echo "$OUT"
check "tampered restore exited non-zero (exit $RC)" "$([ "$RC" -ne 0 ]; echo $?)"
check "the refusal is the sha256 verification" "$(echo "$OUT" | grep -q 'archive verification failed'; echo $?)"
check "the volume is still absent (the refusal created nothing)" \
  "$(dx docker volume inspect "$VOL" >/dev/null 2>&1; [ $? -ne 0 ]; echo $?)"

# 8. the real restore
echo ""
echo "== 8. stack.py restore frontend --from $BDIR"
sp restore frontend --from "$BDIR"; RS=$?
check "restore exited 0 (exit $RS)" "$RS"
check "the volume exists again with compose's labels" \
  "$([ "$(dx docker volume inspect -f '{{index .Labels "com.docker.compose.project"}}/{{index .Labels "com.docker.compose.volume"}}' "$VOL" 2>/dev/null)" = "frontend/openwebui-data" ]; echo $?)"
check "the restored volume holds the marker" \
  "$(dx docker run --rm -v "$VOL:/v:ro" alpine:3.21 cat /v/ops-marker.txt | grep -qx "$MARKER"; echo $?)"

# 9. up again
echo ""
echo "== 9. stack.py up after the restore"
UPOUT="$(sp up 2>&1)"; UP=$?
echo "$UPOUT"
CODE="$(wait_health)"
check "up exited 0 (exit $UP)" "$UP"
check "compose adopted the restored volume without a 'not created by Docker Compose' warning" \
  "$(echo "$UPOUT" | grep -qi 'not created by Docker Compose'; [ $? -ne 0 ]; echo $?)"
check "GET /health returned 200 after the restore (got $CODE)" "$([ "$CODE" = "200" ]; echo $?)"
check "openwebui reads the marker back from its data directory" \
  "$(dx docker exec openwebui cat /app/backend/data/ops-marker.txt | grep -qx "$MARKER"; echo $?)"

# 10. stats
echo ""
echo "== 10. stack.py stats"
OUT="$(sp stats)"; ST=$?
echo "$OUT"
check "stats exited 0 (exit $ST)" "$ST"
check "stats prints a CPU % and a memory figure for openwebui" \
  "$(echo "$OUT" | grep -E '^  openwebui ' | grep -qE '[0-9.]+%.*[0-9.]+[KMG]i?B'; echo $?)"
check "stats says the inference plane is not enabled" "$(echo "$OUT" | grep -q 'inference: NOT ENABLED'; echo $?)"

# 11. recover
echo ""
echo "== 11. stack.py recover frontend"
OUT="$(sp recover frontend)"; RV=$?
echo "$OUT"
CODE="$(wait_health)"
LN_STOP_BK="$(echo "$OUT" | grep -n 'stop --timeout 30 openwebui-backup' | head -n 1 | cut -d: -f1)"
LN_STOP_OW="$(echo "$OUT" | grep -n 'stop --timeout 30 openwebui-stock' | head -n 1 | cut -d: -f1)"
LN_UP_OW="$(echo "$OUT" | grep -n 'up -d --no-deps openwebui-stock' | head -n 1 | cut -d: -f1)"
LN_UP_BK="$(echo "$OUT" | grep -n 'up -d --no-deps openwebui-backup' | head -n 1 | cut -d: -f1)"
check "recover exited 0 (exit $RV)" "$RV"
check "stop order: openwebui-backup (line ${LN_STOP_BK:-?}) before openwebui-stock (line ${LN_STOP_OW:-?})" \
  "$([ -n "$LN_STOP_BK" ] && [ -n "$LN_STOP_OW" ] && [ "$LN_STOP_BK" -lt "$LN_STOP_OW" ]; echo $?)"
check "start order: openwebui-stock (line ${LN_UP_OW:-?}) before openwebui-backup (line ${LN_UP_BK:-?})" \
  "$([ -n "$LN_UP_OW" ] && [ -n "$LN_UP_BK" ] && [ "$LN_UP_OW" -lt "$LN_UP_BK" ]; echo $?)"
check "openwebui passed a HEALTH gate" "$(echo "$OUT" | grep -q '\[ok\] frontend/openwebui-stock (openwebui): healthy'; echo $?)"
check "every container passed its gate" "$(echo "$OUT" | grep -q 'recovered: frontend - every container passed its gate'; echo $?)"
check "GET /health returned 200 after recover (got $CODE)" "$([ "$CODE" = "200" ]; echo $?)"

# 12. the planted failure
echo ""
echo "== 12. PLANTED: a healthcheck that always fails on openwebui-backup; recover must stop and name it"
docker exec -i "$NAME" sh -c 'cat > /tmp/plant.awk' <<'AWK'
{ print }
/^    container_name: openwebui-backup$/ {
  print "    healthcheck:"
  print "      test: [ \"CMD-SHELL\", \"exit 1\" ]"
  print "      interval: 2s"
  print "      timeout: 2s"
  print "      retries: 2"
}
AWK
dw sh -c 'awk -f /tmp/plant.awk frontend/docker-compose.yml > /tmp/c.yml && cp /tmp/c.yml frontend/docker-compose.yml'
check "the plant is in the rendered compose" \
  "$(dw docker compose -f frontend/docker-compose.yml config --format json | grep -q '"exit 1"'; echo $?)"
OUT="$(sp recover frontend)"; RV=$?
echo "$OUT"
check "recover with the plant exited non-zero (exit $RV)" "$([ "$RV" -ne 0 ]; echo $?)"
check "the refusal names frontend, openwebui-backup and 'unhealthy'" \
  "$(echo "$OUT" | grep -q 'refused: recover stopped at frontend: openwebui-backup (openwebui-backup) unhealthy'; echo $?)"
check "openwebui itself passed its gate before the failure" \
  "$(echo "$OUT" | grep -q '\[ok\] frontend/openwebui-stock (openwebui): healthy'; echo $?)"
dw git checkout -q -- frontend/docker-compose.yml

echo ""
echo "== docker ps -a (inside the DinD)"
dx docker ps -a --format '{{.Names}}  {{.Status}}  project={{.Label "com.docker.compose.project"}}'

echo ""
if [ "$FAILS" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
fi
echo "RESULT: FAIL ($FAILS check(s))"
exit 1
