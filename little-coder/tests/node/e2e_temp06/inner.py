"""ao-temp06 e2e, inside the little-coder container (see run.sh). The REAL pi (with its bundled
benchmark-profiles extension and, installed by the image's own entrypoint, the open-terminal-exec
extension) is launched from (a) the GENERATED agent-org worker config and (b) the canonical
interactive coder config, against a stub model; the request bodies the model received are checked.
Prints one line per check; exit 1 if any failed. Writes results.json to $E2E_OUT."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(os.environ["E2E_OUT"])
OUT.mkdir(parents=True, exist_ok=True)
RESULTS: list[dict] = []


def check(name: str, ok: bool, **facts) -> None:
    RESULTS.append({"check": name, "ok": bool(ok), **facts})
    print(("PASS " if ok else "FAIL ") + name + "  " + json.dumps(facts)[:500], flush=True)


def pi_turn(tag: str, config_path: Path, port: int) -> list[dict]:
    sys.path.insert(0, "/e2e-src")
    from littlecoder.agent import AgentRunner
    from littlecoder.config import load_config
    cfg = load_config(config_path)
    cfg.agent.session_dir = f"/tmp/sess-{tag}"

    class _State:
        task_id, session_id, channel, user_id, repo, prompt, plan_only = (
            f"e2e-{tag}", f"s-{tag}", "cli", "u", "r", "Say done.", False)

    class _Ctx:
        state = _State()
    runner = AgentRunner(cfg, journals=None, ot_client=None)
    cmd, _ = runner._build_invocation("Say done.", _Ctx())
    env = runner._build_env(_Ctx(), f"/tmp/ot-events-{tag}.jsonl")
    reqs = OUT / f"model-requests-{tag}.jsonl"
    reqs.unlink(missing_ok=True)
    stub = subprocess.Popen([sys.executable, "/e2e/stub_model.py", str(port), str(reqs)],
                            stderr=open(OUT / f"stub-{tag}.log", "w"))
    time.sleep(1.0)
    os.makedirs("/tmp/ws", exist_ok=True)
    env["LLAMACPP_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    with open(OUT / f"pi-{tag}.jsonl", "w") as fo, open(OUT / f"pi-{tag}.err", "w") as fe:
        p = subprocess.run(cmd, env=env, cwd="/tmp/ws", stdout=fo, stderr=fe, timeout=180)
    stub.terminate()
    bodies = [json.loads(x)["body"] for x in reqs.read_text().splitlines()] if reqs.exists() else []
    check(f"pi[{tag}]: the turn ran and the stub model received a chat request",
          p.returncode == 0 and len(bodies) >= 1, rc=p.returncode, requests=len(bodies),
          LC_TEMPERATURE=env.get("LC_TEMPERATURE"))
    return bodies


def main() -> int:
    gen = Path("/tmp/worker-configs")
    subprocess.run([sys.executable, "/e2e-repo/agent-org/scripts/gen-worker-configs.py", "--out-dir", str(gen)],
                   check=True, capture_output=True, timeout=60)
    # the extension the image's entrypoint installed (LC_ROUTE_EXEC=1) is THIS checkout's
    ext = Path(os.environ["NPM_EXT_DIR"]) / "open-terminal-exec" / "index.ts"
    check("the open-terminal-exec extension in the image is this checkout's, with the temperature hook",
          ext.exists() and "LC_TEMPERATURE" in ext.read_text(), path=str(ext))

    worker = pi_turn("worker", gen / "worker-1" / "little-coder.config.yaml", 8081)
    temps = [b.get("temperature") for b in worker]
    check("WORKER: every model request body carries temperature 0.6 (tools present)",
          bool(temps) and all(t == 0.6 for t in temps) and all(b.get("tools") for b in worker),
          temperatures=temps, tools=[len(b.get("tools") or []) for b in worker])

    coder = pi_turn("coder", Path("/e2e-repo/little-coder/config/little-coder.config.yaml"), 8082)
    temps = [b.get("temperature") for b in coder]
    check("CODER PLANE: the canonical config's request bodies still carry pi's own 0.3",
          bool(temps) and all(t == 0.3 for t in temps), temperatures=temps)

    (OUT / "results.json").write_text(json.dumps(RESULTS, indent=1), encoding="utf-8")
    failed = [r["check"] for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a crash is a failure, with its cause in the log
        import traceback
        traceback.print_exc()
        (OUT / "results.json").write_text(json.dumps(RESULTS + [{"check": "crash", "ok": False}], indent=1))
        sys.exit(2)
