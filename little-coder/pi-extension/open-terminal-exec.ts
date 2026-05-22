/*
 * open-terminal-exec — route little-coder's shell tool into open-terminal.
 *
 * ┌─────────────────────────────────────────────────────────────────────┐
 * │ INTEGRATION POINT (design §1.5, §3.4). The `pi` extension API is not  │
 * │ publicly documented; the registration hook below is best-effort and   │
 * │ must be confirmed against the pinned little-coder version. See        │
 * │ README.md in this directory for the contract and fallbacks.           │
 * └─────────────────────────────────────────────────────────────────────┘
 *
 * Contract: little-coder's command execution must run inside open-terminal
 * (the network-isolated plane) so the inner loop is egress-bounded and every
 * git call hits the git-proxy. The `ot-exec` shim does exactly that and is a
 * drop-in `bash -c` replacement. This extension makes the agent's shell tool
 * call `ot-exec` instead of spawning a local shell.
 */

import { spawnSync } from "node:child_process";

/** Run one command in open-terminal via the ot-exec shim. */
export function runInOpenTerminal(command: string): {
  stdout: string;
  stderr: string;
  exitCode: number;
} {
  // ot-exec is on $PATH in the agent image; it reads LC_OPEN_TERMINAL_URL /
  // LC_OPEN_TERMINAL_KEY / LC_WORKSPACE / LC_EVENT_STREAM from the
  // environment the AgentRunner sets (see littlecoder/agent.py).
  const result = spawnSync("ot-exec", ["-c", command], {
    encoding: "utf-8",
    maxBuffer: 64 * 1024 * 1024,
  });
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    // ot-exec exits with the command's own exit code (124 timeout, 125 if
    // open-terminal is unreachable).
    exitCode: result.status ?? 1,
  };
}

/*
 * Register the override with pi. The shape below assumes pi passes an
 * extension API object that can re-register the built-in `bash` tool. CONFIRM
 * the actual hook name/signature against the pinned little-coder version and
 * adjust — the body (runInOpenTerminal) does not change, only the wiring.
 */
export default function register(pi: any): void {
  if (!pi || typeof pi.registerTool !== "function") {
    // Extension API not as assumed — see README.md. The defence-in-depth
    // network isolation still holds; only instrumentation degrades.
    return;
  }
  pi.registerTool({
    name: "bash",
    description:
      "Run a shell command in the open-terminal workspace plane (little-coder).",
    run: ({ command }: { command: string }) => runInOpenTerminal(command),
  });
}
