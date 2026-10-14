#!/bin/sh
# ao-exechang end-to-end through the REAL exec path (manual / tester evidence; pytest runs it only
# with LC_E2E_DOCKER=1, see tests/test_exechang_e2e.py).
#
#   sh little-coder/tests/node/e2e_exechang/run.sh [little-coder image] [open-terminal image]
#   (from the repo root; defaults little-coder:local and little-coder-open-terminal:local - both
#    only RUN, never built or retagged). E2E_SRC=<dir holding littlecoder/> and
#    E2E_EXT=<index.ts> run other copies (the pre-fix base must fail it).
#
# Two throwaway containers, labelled ai-stack.test=ao-exechang, removed at the end:
#   OT  the open-terminal image the workers' executors run, `--network none` (loopback only),
#       --add-host llama-cpp:127.0.0.1 (the stub model's name; a joined container shares /etc/hosts)
#   LC  the little-coder image, joined to OT's network AND pid namespace (`--network container:`,
#       `--pid container:`), with THIS checkout's ot-exec (src/ on PYTHONPATH), pi extension
#       (installed by the image's own entrypoint, LC_ROUTE_EXEC=1), agent-knowledge and the
#       agent-org worker-config generator mounted in. inner.py drives the real ot-exec against the
#       real open-terminal, then the real pi (built from the generated worker config) against a
#       scripted stub model, and checks /proc for survivors.
# No GPU, no model, no LiteLLM, no ai-stack_* network. Output: <this dir>/out/<run>/.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
LC_SRC=$(cd "$HERE/../../.." && pwd)            # little-coder/
REPO=$(cd "$LC_SRC/.." && pwd)
LC_IMAGE=${1:-little-coder:local}
OT_IMAGE=${2:-little-coder-open-terminal:local}
RUN=${E2E_RUN:-$(date -u +%Y%m%dT%H%M%SZ)}
OUT="$HERE/out/$RUN"
rm -rf "$OUT"
mkdir -p "$OUT"
OT=exechang-ot-$$
LC=exechang-lc-$$
cleanup() { docker rm -f "$LC" "$OT" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
export MSYS_NO_PATHCONV=1

docker run -d --rm --name "$OT" --label ai-stack.test=ao-exechang --network none \
  --add-host llama-cpp:127.0.0.1 --security-opt no-new-privileges:true \
  -e OPEN_TERMINAL_API_KEY=e2e-test-key -e API_KEY=e2e-test-key "$OT_IMAGE" >/dev/null
i=0
until docker exec "$OT" curl -fsS --max-time 2 http://localhost:8000/health >/dev/null 2>&1; do
  i=$((i+1)); [ $i -lt 60 ] || { echo "open-terminal did not become healthy"; exit 1; }; sleep 1
done

set +e
docker run --rm --name "$LC" --label ai-stack.test=ao-exechang \
  --network "container:$OT" --pid "container:$OT" --security-opt no-new-privileges:true \
  -e LC_ROUTE_EXEC=1 -e LLAMACPP_API_KEY=stub -e PYTHONPATH=/e2e-src -e E2E_OUT=/e2e/out/$RUN \
  -v "${E2E_SRC:-$LC_SRC/src}:/e2e-src:ro" \
  -v "${E2E_EXT:-$LC_SRC/pi-extension/open-terminal-exec/index.ts}:/opt/little-coder/pi-extensions/open-terminal-exec/index.ts:ro" \
  -v "$LC_SRC/agent-knowledge:/app/agent-knowledge:ro" \
  -v "$LC_SRC/config:/e2e-repo/little-coder/config:ro" \
  -v "$REPO/agent-org/scripts:/e2e-repo/agent-org/scripts:ro" \
  -v "$HERE:/e2e" \
  "$LC_IMAGE" python3 /e2e/inner.py > "$OUT/inner.log" 2>&1
rc=$?
set -e
docker logs "$OT" > "$OUT/open-terminal.log" 2>&1 || true
tail -n 60 "$OUT/inner.log"
echo "e2e exit $rc; evidence in $OUT"
exit $rc
