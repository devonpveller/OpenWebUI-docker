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
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawnSync } from "node:child_process";

export default function (pi: ExtensionAPI) {
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
    async execute(_id: string, args: { command: string }) {
      // ot-exec is on $PATH; it reads LC_OPEN_TERMINAL_URL / _KEY / WORKSPACE
      // / EVENT_STREAM from the env the AgentRunner set (littlecoder/agent.py).
      const r = spawnSync("ot-exec", ["-c", args.command], {
        encoding: "utf-8",
        maxBuffer: 64 * 1024 * 1024,
      });
      const exitCode = r.status ?? 1;
      let text = r.stdout ?? "";
      if (r.stderr) text += (text ? "\n" : "") + r.stderr;
      if (!text) text = "(no output)";
      return {
        content: [{ type: "text", text }],
        details: { exitCode },
        isError: exitCode !== 0,
      };
    },
  });
}
