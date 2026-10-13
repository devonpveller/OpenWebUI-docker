#!/bin/sh
# ao-flailtime end-to-end (manual; evidence for the test plan, not collected by pytest).
# The REAL little-coder launcher + bundled pi + the entrypoint-installed open-terminal-exec extension
# (LC_ROUTE_EXEC=1), the REAL AgentRunner._run_cli + flail watcher from the littlecoder source mounted
# at /lcsrc (base or tip), a scripted stub model on 127.0.0.1:8080 (llama-cpp resolves there via
# --add-host) and a stub ot-exec that RUNS each command locally (real sleep/echo).
#
#   MSYS_NO_PATHCONV=1 docker run --rm --network none --add-host llama-cpp:127.0.0.1 \
#     -e LC_ROUTE_EXEC=1 -e LLAMACPP_API_KEY=stub -e TAG=tip-tooltime -e SCENARIO=tooltime \
#     -v <this dir>:/e2e -v <littlecoder src dir>:/lcsrc:ro <little-coder image> sh /e2e/run.sh
# SCENARIO: tooltime | thinking | batch | retry (stub_model.py). FLAIL_SECONDS (default 20) is the
# time rule's threshold, scaled down from 900 so a run takes about a minute.
# No network (--network none), no GPU, no model, no LiteLLM. Output: <this dir>/out/<TAG>/.
set -u
export E2E_OUT=/e2e/out/$TAG
rm -rf "$E2E_OUT"; mkdir -p "$E2E_OUT" /tmp/e2e-bin /tmp/ws
cat > /tmp/e2e-bin/ot-exec <<'STUB'
#!/bin/sh
[ "$1" = "-c" ] && shift
echo "start $(date +%s.%N) $1" >> "$E2E_OUT/otexec.log"
sh -c "$1"; rc=$?
echo "end $(date +%s.%N) rc=$rc" >> "$E2E_OUT/otexec.log"
exit $rc
STUB
chmod +x /tmp/e2e-bin/ot-exec
python3 /e2e/stub_model.py 8080 "$SCENARIO" "$E2E_OUT/model.log" 2> "$E2E_OUT/model.err" &
sleep 1
export PATH=/tmp/e2e-bin:$PATH LITTLE_CODER_PERMISSION_MODE=accept-all LLAMACPP_BASE_URL=http://llama-cpp:8080/v1
export PYTHONPATH=/lcsrc
EXT="$(npm root -g)/little-coder/.pi/extensions/open-terminal-exec/index.ts"
{
  echo "ot-exec resolves to: $(command -v ot-exec)"
  echo "installed extension spawnSync calls: $(grep -c 'spawnSync(' "$EXT")"
  echo "installed extension sequential: $(grep -c '^ *executionMode: "sequential"' "$EXT")"
} > "$E2E_OUT/info.txt"
python3 /e2e/driver.py > "$E2E_OUT/driver.out" 2> "$E2E_OUT/driver.err"
echo "driver exit $?" >> "$E2E_OUT/info.txt"
cat "$E2E_OUT/driver.out"
