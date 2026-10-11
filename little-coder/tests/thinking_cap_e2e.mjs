// ao-thinkon: drive the REAL little-coder launcher + pi (the image's, its real extension discovery)
// against a model server and report what happened to the thought. Numbers only on stdout (no
// thought text). No dependencies; run INSIDE the agent image (node 22), offline:
//
//   node thinking_cap_e2e.mjs <mode> [--chars N] [--port P] [--secs S]
//
// mode stub-long     a stub OpenAI-compatible server on 127.0.0.1:<port> streams ONE reasoning block
//                    of --chars characters (varied, non-repetitive sentences), then the answer "done".
// mode stub-endless  the stub streams reasoning forever (never closes it); the run is stopped after
//                    --secs. Shows there is no client-side bound at any length.
// mode external      no stub: the model server is whatever models.json points at (e.g. a real
//                    llama.cpp with --reasoning-budget, sharing this container's network namespace).
//
// models.json (~/.config/little-coder, from /app/config/models.json) must point the llamacpp
// provider at http://127.0.0.1:<port>/v1. Prints one JSON line:
//   {mode, exit, elapsed_s, requests:[{max_tokens, reasoning_effort, ...}], assistants:[{thinking_chars,
//    text_chars, stopReason}], thinking_delta_chars, budget_exceeded_msgs, user_msgs, extensions:[...]}
import { spawn } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";

const argv = process.argv.slice(2);
const mode = argv[0];
const opt = (name, dflt) => {
  const i = argv.indexOf(name);
  return i >= 0 ? Number(argv[i + 1]) : dflt;
};
const CHARS = opt("--chars", 30000);
const PORT = opt("--port", 18080);
const SECS = opt("--secs", 300);
if (!["stub-long", "stub-endless", "external"].includes(mode)) {
  console.error("mode: stub-long | stub-endless | external");
  process.exit(2);
}

// ── the stub model server ──────────────────────────────────────────────────────────────────────
const requests = [];
let streamedReasoning = 0;
const SUBJECTS = ["the admission counter", "the release path", "the timeout handling", "the retry loop",
  "the config loader", "the queue depth metric", "the slot reservation", "the shutdown drain",
  "the health probe", "the error mapping", "the log formatter", "the token refresh"];
const VERBS = ["reads", "updates", "validates", "returns", "locks", "records", "skips", "retries"];
function sentence(i) {
  // varied text: a numbered step over a rotating subject/verb with the step's own arithmetic, so no
  // two sentences are equal (non-repetitive by construction).
  const s = SUBJECTS[i % SUBJECTS.length];
  const v = VERBS[(i * 7) % VERBS.length];
  return `Step ${i}: ${s} ${v} value ${i * 37 % 1009} before line ${100 + i * 3}, so case ${i} holds. `;
}
function chunk(delta, finish = null) {
  return `data: ${JSON.stringify({ id: "stub", object: "chat.completion.chunk", created: 0, model: "stub",
    choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`;
}
function startStub() {
  const srv = createServer((req, res) => {
    let body = "";
    req.on("data", (d) => { body += d; });
    req.on("end", () => {
      if (req.method === "GET") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ object: "list", data: [{ id: "local-large", object: "model" }] }));
        return;
      }
      let j = {};
      try { j = JSON.parse(body); } catch { /* keep {} */ }
      const n = requests.length;
      requests.push({ model: j.model, stream: j.stream, max_tokens: j.max_tokens ?? null,
        max_completion_tokens: j.max_completion_tokens ?? null, reasoning_effort: j.reasoning_effort ?? null,
        chat_template_kwargs: j.chat_template_kwargs ?? null, messages: (j.messages || []).length });
      res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
      let closed = false;
      res.on("close", () => { closed = true; });
      res.write(chunk({ role: "assistant", content: "" }));
      const finish = () => {
        if (closed) return;
        res.write(chunk({ content: "done" }));
        res.write(chunk({}, "stop"));
        res.write("data: [DONE]\n\n");
        res.end();
      };
      if (n > 0 && mode === "stub-long") {          // any later request: a short reply
        res.write(chunk({ reasoning_content: "Short follow-up thought." }));
        finish();
        return;
      }
      let i = 0;
      let sent = 0;
      const tick = () => {
        if (closed) return;
        let piece = "";
        // endless: big, slow chunks (pi's json mode writes the partial message with every delta)
        const size = mode === "stub-endless" ? 10000 : 400;
        while (piece.length < size) piece += sentence(i++);
        if (mode === "stub-long" && sent + piece.length > CHARS) piece = piece.slice(0, CHARS - sent);
        if (piece.length) {
          res.write(chunk({ reasoning_content: piece }));
          sent += piece.length;
          streamedReasoning += piece.length;
        }
        if (mode === "stub-long" && sent >= CHARS) { finish(); return; }
        setTimeout(tick, mode === "stub-endless" ? 250 : 2);
      };
      tick();
    });
  });
  return new Promise((r) => srv.listen(PORT, "127.0.0.1", () => r(srv)));
}

// ── the real launcher ──────────────────────────────────────────────────────────────────────────
function piExtensions() {
  // the extension list pi was actually started with (Linux /proc; the launcher spawns pi as a child)
  try {
    for (const pid of readdirSync("/proc").filter((d) => /^\d+$/.test(d))) {
      let cmd;
      try { cmd = readFileSync(`/proc/${pid}/cmdline`, "utf8").split("\0"); } catch { continue; }
      if (!cmd.includes("--extension")) continue;
      const ext = [];
      cmd.forEach((a, k) => { if (a === "--extension") ext.push(basename(dirname(cmd[k + 1]))); });
      return ext;
    }
  } catch { /* not Linux */ }
  return null;
}

const srv = mode === "external" ? null : await startStub();
const cwd = mkdtempSync(join(tmpdir(), "ao-thinkon-"));
const t0 = Date.now();
const child = spawn("little-coder", ["--print", "--mode", "json", "--model", "llamacpp/local-large",
  "--no-approve", "--no-session", "Plan the fix for the queue limit in two sentences. Do not use tools."],
  { cwd, stdio: ["ignore", "pipe", "pipe"], env: process.env });
let buf = "";
let extensions = null;
const assistants = [];
const userMsgs = [];
let deltaChars = 0;
let stderrBytes = 0;
child.stderr.on("data", (d) => { stderrBytes += d.length; });
child.stdout.on("data", (d) => {
  if (!extensions) extensions = piExtensions();
  buf += d;
  let k;
  while ((k = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, k);
    buf = buf.slice(k + 1);
    let ev;
    try { ev = JSON.parse(line); } catch { continue; }
    const ame = ev.assistantMessageEvent;
    if (ev.type === "message_update" && ame?.type === "thinking_delta") deltaChars += (ame.delta || "").length;
    if (ev.type === "message_end" && ev.message) {
      const m = ev.message;
      const blocks = Array.isArray(m.content) ? m.content : [{ type: "text", text: String(m.content ?? "") }];
      if (m.role === "assistant") {
        assistants.push({
          thinking_chars: blocks.filter((b) => b.type === "thinking").reduce((a, b) => a + (b.thinking || "").length, 0),
          text_chars: blocks.filter((b) => b.type === "text").reduce((a, b) => a + (b.text || "").length, 0),
          tool_calls: blocks.filter((b) => b.type === "toolCall").length,
          stopReason: m.stopReason ?? null,
        });
      } else if (m.role === "user") {
        const t = blocks.map((b) => b.text || "").join(" ");
        userMsgs.push({ chars: t.length, budget_exceeded: t.includes("[thinking budget exceeded]") });
      }
    }
  }
});
const killer = setTimeout(() => { child.kill("SIGTERM"); }, SECS * 1000);
const exit = await new Promise((r) => child.on("close", (code, sig) => r(code ?? sig)));
clearTimeout(killer);
if (srv) srv.close();
process.stdout.write(JSON.stringify({
  mode, chars: mode === "stub-long" ? CHARS : null, exit, elapsed_s: Math.round((Date.now() - t0) / 100) / 10,
  stub_streamed_reasoning_chars: srv ? streamedReasoning : null, requests, assistants,
  thinking_delta_chars: deltaChars, user_msgs: userMsgs,
  budget_exceeded_msgs: userMsgs.filter((u) => u.budget_exceeded).length, stderr_bytes: stderrBytes,
  extensions,
}) + "\n");
process.exit(0);
