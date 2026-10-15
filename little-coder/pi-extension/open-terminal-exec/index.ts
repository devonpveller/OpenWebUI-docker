/*
 * open-terminal-exec — route little-coder's shell tool into open-terminal.
 *
 * Overrides the built-in `bash` tool so the agent's command execution runs in
 * the open-terminal workspace plane (via the `ot-exec` shim) instead of
 * locally — design §1.5, §3.3, §3.4. Every command then runs network-isolated
 * and every `git` call is policed by the git-proxy.
 *
 * The pi extension API used here (`pi.registerTool` with an `execute` that
 * returns `{ content: [{type,text}], details, isError }`) matches the bundled
 * little-coder extensions (e.g. extra-tools). This file is installed INTO
 * little-coder's own .pi/extensions/ dir by docker/entrypoint-agent.sh when
 * LC_ROUTE_EXEC=1, so pi discovers it and its (zero) runtime imports resolve.
 *
 * Uses only `import type` (erased at compile) + node builtins, so there is no
 * runtime dependency to resolve.
 *
 * ao-asyncexec (2026-10-11): the command runs with an ASYNC `spawn`, never
 * `spawnSync`. A synchronous spawn froze pi's event loop for the whole
 * command, and pi's `--mode json` writer only flushes a line per write
 * callback, so the call's `tool_execution_start` reached the events file only
 * after the command had ended: the bridge's command watch, the daemon's tool
 * counters and the flail guard never saw a running command. What stays as it
 * was under spawnSync:
 *   - one command at a time: `executionMode: "sequential"` (pi-agent-core
 *     runs a batch that contains a sequential tool call by call, emitting each
 *     start just before that call executes);
 *   - the output cap: 64 MB counted over stdout + stderr together; past it the
 *     child gets SIGTERM and the output read so far is kept (spawnSync's
 *     maxBuffer/ENOBUFS behaviour);
 *   - the result: stdout, then stderr after a newline, "(no output)" when both
 *     are empty, details.exitCode = the exit status, or 1 when there is none
 *     (killed by a signal, could not start), isError = exitCode !== 0;
 *   - timeouts: ot-exec applies LC_EXEC_TIMEOUT (it kills the command's whole
 *     process tree in open-terminal, then exits 124 with a fixed message).
 * New: pi's abort signal is honoured (the child gets SIGTERM; the result says
 * "Command aborted"). The child stays in pi's process group, so the daemon's
 * process-group kill on cancel still reaches it exactly as before.
 *
 * ao-exechang (2026-10-14):
 *   - pi takes `isError` only from a THROWN error: a returned
 *     `{isError: true}` is recorded as a success (bake-off sessions: every
 *     1,800-s timeout reached the model as "(no output)", isError false). So an
 *     ot-exec timeout (exit 124 AND its fixed "[ot-exec] timed out after"
 *     line) is thrown: the model gets the output so far plus the message as an
 *     error result. Any other exit, a user's own `timeout` exit 124 included,
 *     is returned exactly as before.
 *   - backstop: if ot-exec itself has not ended LC_EXEC_TIMEOUT +
 *     LC_EXEC_BACKSTOP_GRACE (default 120) seconds after it started, it gets
 *     SIGTERM (it then kills the command in open-terminal), and SIGKILL 30 s
 *     later; the call fails with a fixed message. No call outlives that.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";

const MAX_OUTPUT_BYTES = 64 * 1024 * 1024;
const TIMEOUT_EXIT = 124;
const TIMEOUT_MARKER = "[ot-exec] timed out after"; // littlecoder/otexec.py TIMEOUT_MARKER
const DEFAULT_EXEC_TIMEOUT_S = 1800; // ot-exec's own default when LC_EXEC_TIMEOUT is unset
const DEFAULT_BACKSTOP_GRACE_S = 120;
const BACKSTOP_KILL_AFTER_MS = 30_000;

function envSeconds(name: string, fallback: number): number {
  const v = Number(process.env[name]);
  return Number.isFinite(v) && v > 0 ? v : fallback;
}

/** ot-exec's own bound fired: it killed the command and wrote its fixed message, which starts a
 * line of ot-exec's OWN stderr (the command's output arrives on stdout: open-terminal's PTY). */
function isOtExecTimeout(exitCode: number, stderr: string): boolean {
  return exitCode === TIMEOUT_EXIT && (stderr.startsWith(TIMEOUT_MARKER) || stderr.includes("\n" + TIMEOUT_MARKER));
}

type ExecResult = {
  content: { type: "text"; text: string }[];
  details: { exitCode: number };
  isError: boolean;
};

function result(stdout: string, stderr: string, exitCode: number, note?: string): ExecResult {
  let text = stdout;
  if (stderr) text += (text ? "\n" : "") + stderr;
  if (!text) text = "(no output)";
  if (note) text += "\n\n" + note;
  return { content: [{ type: "text", text }], details: { exitCode }, isError: exitCode !== 0 };
}

function runOtExec(command: string, signal: AbortSignal | undefined): Promise<ExecResult> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      resolve(result("", "", 1, "Command aborted"));
      return;
    }
    // ot-exec is on $PATH; it reads LC_OPEN_TERMINAL_URL / _KEY / WORKSPACE
    // / EVENT_STREAM from the env the AgentRunner set (littlecoder/agent.py).
    const child = spawn("ot-exec", ["-c", command], { stdio: ["pipe", "pipe", "pipe"] });
    // A stream error (EPIPE on stdin, a failed spawn) must never become an
    // uncaught exception in pi's process; the child's 'close'/'error' decide.
    for (const s of [child.stdin, child.stdout, child.stderr]) s?.on("error", () => {});
    child.stdin?.end(); // spawnSync gave the child an empty, closed stdin
    const out: Buffer[] = [];
    const err: Buffer[] = [];
    let total = 0;
    let capped = false;
    let aborted = false;
    let done = false;
    const take = (sink: Buffer[]) => (chunk: Buffer) => {
      if (capped) return;
      sink.push(chunk);
      total += chunk.length;
      if (total > MAX_OUTPUT_BYTES) {
        capped = true;
        child.kill("SIGTERM");
      }
    };
    child.stdout?.on("data", take(out));
    child.stderr?.on("data", take(err));
    const onAbort = () => {
      aborted = true;
      child.kill("SIGTERM");
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    // Backstop: ot-exec bounds the command; this bounds ot-exec.
    const backstopS =
      envSeconds("LC_EXEC_TIMEOUT", DEFAULT_EXEC_TIMEOUT_S) +
      envSeconds("LC_EXEC_BACKSTOP_GRACE", DEFAULT_BACKSTOP_GRACE_S);
    let stalled = false;
    let hardKill: ReturnType<typeof setTimeout> | undefined;
    const backstop = setTimeout(() => {
      stalled = true;
      child.kill("SIGTERM");
      hardKill = setTimeout(() => child.kill("SIGKILL"), BACKSTOP_KILL_AFTER_MS);
    }, backstopS * 1000);
    const finish = (status: number | null) => {
      if (done) return;
      done = true;
      clearTimeout(backstop);
      if (hardKill) clearTimeout(hardKill);
      signal?.removeEventListener("abort", onAbort);
      const stderr = Buffer.concat(err).toString("utf-8");
      const r = result(
        Buffer.concat(out).toString("utf-8"),
        stderr,
        status ?? 1,
        aborted
          ? "Command aborted"
          : stalled
            ? `[open-terminal-exec] ot-exec did not finish within ${Math.round(backstopS)} s and was stopped; ` +
              "the command's result is unknown. Run a narrower command, or background a long job: " +
              "nohup CMD > /tmp/x.log 2>&1 &  then poll the log."
            : undefined,
      );
      if (stalled || (!aborted && isOtExecTimeout(r.details.exitCode, stderr))) reject(new Error(r.content[0].text));
      else resolve(r);
    };
    // could not start (ENOENT ...): spawnSync returned no output and no status.
    // An 'error' for a child that DID start (e.g. a failed kill) is not the end:
    // its 'close' still comes, so one command at a time holds.
    child.on("error", () => {
      if (child.pid === undefined) finish(null);
    });
    child.on("close", (code: number | null) => finish(code));
  });
}

/** ao-temp06: the per-call sampling temperature a deployment asks for (config `agent.temperature`
 * -> agent.py -> LC_TEMPERATURE). Unset/blank/not a number in [0, 2] = no override. */
export function requestedTemperature(env: NodeJS.ProcessEnv = process.env): number | undefined {
  const raw = (env.LC_TEMPERATURE ?? "").trim();
  if (raw === "") return undefined;
  const v = Number(raw);
  return Number.isFinite(v) && v >= 0 && v <= 2 ? v : undefined;
}

export default function (pi: ExtensionAPI) {
  // ao-temp06: little-coder's benchmark-profiles extension injects `temperature` 0.3 (its
  // default_model_profile; our models match no per-model profile) into every provider request. When
  // LC_TEMPERATURE is set (the agent-org worker configs only) replace it. Only where benchmark-profiles
  // already put a temperature: its provider guard (hosted reasoning models reject the field) stays the
  // one decision. This extension loads after benchmark-profiles (extension dirs sort by name), so its
  // handler runs last; tests/node/e2e_temp06 proves the order against the real pi.
  pi.on("before_provider_request", async (event: any) => {
    const t = requestedTemperature();
    const payload = event?.payload;
    if (t === undefined || !payload || typeof payload !== "object" || !("temperature" in payload)) return;
    return { ...payload, temperature: t };
  });

  pi.registerTool({
    name: "bash",
    label: "Bash",
    description:
      "Run a shell command in the open-terminal workspace plane. " +
      "Execution is network-isolated and every git call is policed by the " +
      "git-proxy. Use this for builds, tests, and git.",
    parameters: {
      type: "object",
      properties: {
        command: {
          type: "string",
          description: "The shell command to run.",
        },
      },
      required: ["command"],
    } as any,
    executionMode: "sequential",
    async execute(_id: string, args: { command: string }, signal?: AbortSignal) {
      return await runOtExec(args.command, signal);
    },
  });
}
