#!/bin/sh
# ao-asyncexec end-to-end (manual; evidence for the test plan, not collected by pytest).
# The REAL little-coder launcher + bundled pi + the entrypoint-installed open-terminal-exec extension,
# a scripted stub model on 127.0.0.1:8080 (llama-cpp resolves there via --add-host), and a stub
# ot-exec that runs the command locally. Snapshots pi's --mode json stdout every 0.5 s, named with
# the wall clock, so analyze.py can pick the ones taken while a command was running.
#
#   MSYS_NO_PATHCONV=1 docker run --rm --network none --add-host llama-cpp:127.0.0.1 \
#     -e LC_ROUTE_EXEC=1 -e LLAMACPP_API_KEY=stub -e TAG=tip-sleep20 -e SCENARIO=sleep20 \
#     -v <this dir>:/e2e [-v <other index.ts>:/opt/little-coder/pi-extensions/open-terminal-exec/index.ts:ro] \
#     little-coder:wt-<id> sh /e2e/run.sh
#   (cd agent-org/agent-bridge && python <this dir>/analyze.py <this dir>/out/tip-sleep20)
# SCENARIO: sleep20 (one `bash sleep 20`) | two (`sleep 6; echo A` + `echo B` in one message).
# No network (--network none), no GPU, no model, no LiteLLM. Output: <this dir>/out/<TAG>/.
set -u
export E2E_OUT=/e2e/out/$TAG
rm -rf "$E2E_OUT"; mkdir -p "$E2E_OUT" /tmp/e2e-bin
cat > /tmp/e2e-bin/ot-exec <<'STUB'
#!/bin/sh
[ "$1" = "-c" ] && shift
echo "start $(date +%s.%N) $1" >> "$E2E_OUT/otexec.log"
sh -c "$1"; rc=$?
echo "end $(date +%s.%N) rc=$rc" >> "$E2E_OUT/otexec.log"
exit $rc
STUB
chmod +x /tmp/e2e-bin/ot-exec
python3 /e2e/stub_model.py 8080 "$SCENARIO" > "$E2E_OUT/model.log" 2>&1 &
sleep 1
export PATH=/tmp/e2e-bin:$PATH LITTLE_CODER_PERMISSION_MODE=accept-all LLAMACPP_BASE_URL=http://llama-cpp:8080/v1
echo "ot-exec resolves to: $(command -v ot-exec)" > "$E2E_OUT/info.txt"
EXT="$(npm root -g)/little-coder/.pi/extensions/open-terminal-exec/index.ts"
echo "installed extension spawnSync calls: $(grep -c 'spawnSync(' "$EXT")" >> "$E2E_OUT/info.txt"
echo "installed extension sequential: $(grep -c '^ *executionMode: "sequential"' "$EXT")" >> "$E2E_OUT/info.txt"
mkdir -p /tmp/ws && cd /tmp/ws
little-coder --model llamacpp/local-large --print --mode json \
  --append-system-prompt /app/agent-knowledge/environment.md \
  --exclude-tools ShellSession,ShellStart,BrowserNavigate,webfetch,websearch \
  --no-approve --no-session "Run the command." > "$E2E_OUT/pi.jsonl" 2> "$E2E_OUT/pi.err" &
PI=$!
i=0
while kill -0 $PI 2>/dev/null && [ $i -lt 120 ]; do
  sleep 0.5; i=$((i+1))
  ts=$(date +%s.%N); cp "$E2E_OUT/pi.jsonl" "$E2E_OUT/snap-$(printf %03d $i)-$ts.jsonl"
done
wait $PI; echo "pi exit $?" >> "$E2E_OUT/info.txt"
