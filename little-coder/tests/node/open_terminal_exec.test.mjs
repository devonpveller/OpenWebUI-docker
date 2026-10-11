// Contract test for pi-extension/open-terminal-exec (ao-asyncexec).
//
// Runs the extension's real `bash` execute() against a stub `ot-exec`
// (stub-ot-exec.mjs). The spawn of "ot-exec" is redirected to
// `node stub-ot-exec.mjs` by wrapping node:child_process (CommonJS object +
// syncBuiltinESMExports, so the extension's ESM named import sees it); the
// argv the extension passes is recorded and asserted, nothing else changes.
//
// Run: node --experimental-strip-types --test tests/node/open_terminal_exec.test.mjs
// OTX_EXTENSION=<path to index.ts> runs it against another copy (e.g. the
// pre-fix spawnSync version, which must fail the event-loop, sequential and
// abort cases and pass the rest).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire, syncBuiltinESMExports } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const STUB = resolve(here, "stub-ot-exec.mjs");
const EXT = process.env.OTX_EXTENSION
  ? resolve(process.env.OTX_EXTENSION)
  : resolve(here, "..", "..", "pi-extension", "open-terminal-exec", "index.ts");
const CAP = 64 * 1024 * 1024;

// ── redirect "ot-exec" to the stub ───────────────────────────────────────────
const require = createRequire(import.meta.url);
const cp = require("node:child_process");
const calls = [];
let missing = false; // when set, "ot-exec" maps to a binary that does not exist
const realSpawn = cp.spawn;
const realSpawnSync = cp.spawnSync;
function redirect(file, args) {
  calls.push({ file, args });
  if (file !== "ot-exec") return [file, args];
  if (missing) return ["ot-exec-does-not-exist-ao-asyncexec", args];
  return [process.execPath, [STUB, ...args]];
}
cp.spawn = function (file, args, opts) {
  const [f, a] = redirect(file, args);
  return realSpawn.call(this, f, a, opts);
};
cp.spawnSync = function (file, args, opts) {
  const [f, a] = redirect(file, args);
  return realSpawnSync.call(this, f, a, opts);
};
syncBuiltinESMExports();

const tools = [];
const mod = await import(pathToFileURL(EXT).href);
mod.default({ registerTool: (def) => tools.push(def) });
const bash = tools.find((t) => t.name === "bash");

const run = (spec, signal) => bash.execute("call-1", { command: JSON.stringify(spec) }, signal);
const text = (r) => r.content.map((c) => c.text).join("");

// ── the fix ──────────────────────────────────────────────────────────────────

test("the event loop keeps running while the command runs (spawnSync froze it)", async () => {
  let ticks = 0;
  const iv = setInterval(() => ticks++, 50);
  const t0 = Date.now();
  const p = run({ stdout: "ok\n", sleep_ms: 1500 });
  const ticksRightAfterCall = ticks;
  const r = await p;
  clearInterval(iv);
  assert.ok(Date.now() - t0 >= 1400, "the stub really slept");
  assert.equal(ticksRightAfterCall, 0);
  assert.ok(ticks >= 10, `interval ticked ${ticks} times during a 1.5 s command; a blocked loop gives 0`);
  assert.equal(text(r), "ok\n");
});

test("execute() returns to the caller before the command ends (a pending promise)", async () => {
  let settled = false;
  const p = run({ sleep_ms: 800 }).then((r) => ((settled = true), r));
  await new Promise((res) => setTimeout(res, 200));
  assert.equal(settled, false, "still running after 200 ms");
  await p;
  assert.equal(settled, true);
});

test("the tool is registered sequential: one command at a time, as under spawnSync", () => {
  assert.equal(bash.executionMode, "sequential");
});

test("pi's abort signal stops the command", async () => {
  const ac = new AbortController();
  const t0 = Date.now();
  setTimeout(() => ac.abort(), 300);
  const r = await run({ stdout: "before\n", sleep_ms: 20000 }, ac.signal);
  assert.ok(Date.now() - t0 < 5000, "returned soon after the abort, not after 20 s");
  assert.equal(r.isError, true);
  assert.equal(r.details.exitCode, 1);
  assert.match(text(r), /^before\n/);
  assert.match(text(r), /Command aborted$/);
});

test("an already-aborted signal runs nothing", async () => {
  const ac = new AbortController();
  ac.abort();
  const n = calls.length;
  const r = await run({ stdout: "x" }, ac.signal);
  assert.equal(calls.length, n, "ot-exec was not spawned");
  assert.equal(r.isError, true);
  assert.match(text(r), /Command aborted/);
});

// ── unchanged semantics (these pass at base too) ─────────────────────────────

test("argv: ot-exec -c <command>, the command passed verbatim", async () => {
  const n = calls.length;
  const spec = { stdout: "q 'x' \"y\" $HOME `z`\n" };
  await run(spec);
  assert.deepEqual(calls[n], { file: "ot-exec", args: ["-c", JSON.stringify(spec)] });
});

test("stdout, then stderr after a newline; exit 0 is not an error", async () => {
  const r = await run({ stdout: "out\n", stderr: "err\n" });
  assert.deepEqual(r, {
    content: [{ type: "text", text: "out\n\nerr\n" }],
    details: { exitCode: 0 },
    isError: false,
  });
});

test("stderr only: no leading newline", async () => {
  const r = await run({ stderr: "only err\n", exit: 2 });
  assert.equal(text(r), "only err\n");
  assert.deepEqual(r.details, { exitCode: 2 });
  assert.equal(r.isError, true);
});

test("no output at all reads (no output)", async () => {
  const r = await run({});
  assert.equal(text(r), "(no output)");
  assert.equal(r.isError, false);
});

test("a non-zero exit is passed through and is an error", async () => {
  const r = await run({ stdout: "failing\n", exit: 3 });
  assert.equal(text(r), "failing\n");
  assert.deepEqual(r.details, { exitCode: 3 });
  assert.equal(r.isError, true);
});

test("ot-exec's own timeout (exit 124 + partial output) passes through unchanged", async () => {
  const r = await run({ stdout: "partial\n", exit: 124 });
  assert.equal(text(r), "partial\n");
  assert.deepEqual(r.details, { exitCode: 124 });
  assert.equal(r.isError, true);
});

test("ot-exec that cannot start: (no output), exit 1", async () => {
  missing = true;
  try {
    const r = await run({ stdout: "never" });
    assert.deepEqual(r, {
      content: [{ type: "text", text: "(no output)" }],
      details: { exitCode: 1 },
      isError: true,
    });
  } finally {
    missing = false;
  }
});

test("multi-byte UTF-8 split across reads decodes intact", async () => {
  const r = await run({ split_utf8: true });
  assert.equal(text(r), "café ✓\n");
});

test("output cap: past 64 MB the command is killed, output kept, exit 1", async () => {
  const t0 = Date.now();
  const r = await run({ out_mb: 66, sleep_ms: 30000 });
  assert.ok(Date.now() - t0 < 25000, "killed at the cap, not left to sleep 30 s");
  const len = text(r).length;
  assert.ok(len > CAP && len <= CAP + 2 * 1024 * 1024, `kept ${len} bytes, around the 64 MB cap`);
  assert.deepEqual(r.details, { exitCode: 1 });
  assert.equal(r.isError, true);
});

test("output cap counts stdout and stderr together (spawnSync maxBuffer)", async () => {
  const r = await run({ err_mb: 40, out_mb: 40, sleep_ms: 30000 });
  const [o, e] = text(r).split("\n");
  assert.equal(e.length, 40 * 1024 * 1024, "stderr was written first, all of it kept");
  assert.ok(o.length > 0 && o.length + e.length <= CAP + 2 * 1024 * 1024, "stdout cut at the combined cap");
  assert.deepEqual(r.details, { exitCode: 1 });
});
