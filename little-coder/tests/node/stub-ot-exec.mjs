// Stub `ot-exec` for the open-terminal-exec contract test (ao-asyncexec).
// Called as: node stub-ot-exec.mjs -c <JSON spec>. The spec says what to do:
//   {"stdout": str, "stderr": str, "exit": int, "sleep_ms": int,
//    "out_mb": int, "err_mb": int, "split_utf8": true}
// It never runs the command text; it only acts out the spec.
import { setTimeout as sleep } from "node:timers/promises";

const [flag, raw] = process.argv.slice(2);
if (flag !== "-c") {
  process.stderr.write("stub ot-exec: expected -c\n");
  process.exit(2);
}
const spec = JSON.parse(raw);

function write(stream, buf) {
  return new Promise((res) => (stream.write(buf) ? res() : stream.once("drain", res)));
}

if (spec.stdout) await write(process.stdout, spec.stdout);
if (spec.stderr) await write(process.stderr, spec.stderr);
if (spec.split_utf8) {
  const b = Buffer.from("café ✓\n", "utf-8");
  await write(process.stdout, b.subarray(0, 4)); // ends mid-character
  await sleep(150);
  await write(process.stdout, b.subarray(4));
}
const mb = Buffer.alloc(1024 * 1024, 0x61);
for (let i = 0; i < (spec.err_mb || 0); i++) await write(process.stderr, mb);
for (let i = 0; i < (spec.out_mb || 0); i++) await write(process.stdout, mb);
if (spec.sleep_ms) await sleep(spec.sleep_ms);
process.exitCode = spec.exit ?? 0;
