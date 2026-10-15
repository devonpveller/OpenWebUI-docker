#!/bin/sh
# ao-temp06 end-to-end: worker requests carry temperature 0.6 through the REAL path (manual / tester
# evidence; pytest runs it only with LC_E2E_DOCKER=1, see tests/test_temp06_e2e.py).
#
#   sh little-coder/tests/node/e2e_temp06/run.sh [little-coder image]
#   (from the repo root; default little-coder:local - only RUNS, never built or retagged).
#   E2E_SRC=<dir holding littlecoder/> and E2E_EXT=<index.ts> run other copies (the pre-fix base
#   must fail it).
#
# ONE throwaway container, labelled ai-stack.test=ao-temp06, `--network none` (loopback only),
# --add-host llama-cpp:127.0.0.1, removed at the end. It mounts THIS checkout's littlecoder source,
# open-terminal-exec extension (installed by the image's own entrypoint, LC_ROUTE_EXEC=1), canonical
# config and the agent-org worker-config generator. inner.py generates the worker configs, launches
# the real pi from the generated worker config and from the canonical coder config against a stub
# model, and checks the request bodies. No GPU, no model, no LiteLLM, no ai-stack_* network.
# Output: <this dir>/out/<run>/.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
LC_SRC=$(cd "$HERE/../../.." && pwd)            # little-coder/
REPO=$(cd "$LC_SRC/.." && pwd)
LC_IMAGE=${1:-little-coder:local}
RUN=${E2E_RUN:-$(date -u +%Y%m%dT%H%M%SZ)}
OUT="$HERE/out/$RUN"
rm -rf "$OUT"
mkdir -p "$OUT"
LC=temp06-lc-$$
cleanup() { docker rm -f "$LC" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
export MSYS_NO_PATHCONV=1

set +e
docker run --rm --name "$LC" --label ai-stack.test=ao-temp06 --network none \
  --add-host llama-cpp:127.0.0.1 --security-opt no-new-privileges:true \
  -e LC_ROUTE_EXEC=1 -e LLAMACPP_API_KEY=stub -e PYTHONPATH=/e2e-src -e E2E_OUT=/e2e/out/$RUN \
  -e NPM_EXT_DIR=/usr/lib/node_modules/little-coder/.pi/extensions \
  -v "${E2E_SRC:-$LC_SRC/src}:/e2e-src:ro" \
  -v "${E2E_EXT:-$LC_SRC/pi-extension/open-terminal-exec/index.ts}:/opt/little-coder/pi-extensions/open-terminal-exec/index.ts:ro" \
  -v "$LC_SRC/agent-knowledge:/app/agent-knowledge:ro" \
  -v "$LC_SRC/config:/e2e-repo/little-coder/config:ro" \
  -v "$REPO/agent-org/scripts:/e2e-repo/agent-org/scripts:ro" \
  -v "$HERE:/e2e" \
  "$LC_IMAGE" python3 /e2e/inner.py > "$OUT/inner.log" 2>&1
rc=$?
set -e
tail -n 40 "$OUT/inner.log"
echo "e2e exit $rc; evidence in $OUT"
exit $rc
