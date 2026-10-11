// Build-time removal of upstream little-coder's client-side thinking cap (ao-thinkon, 2026-10-11).
// Run by Dockerfile.agent as root, after the npm install, apply-pi-patches.mjs and the COPY of our
// own pi extensions:
//
//   node remove-thinking-cap.mjs <park dir> [<our extension dir> ...]
//
// Upstream's bundled `.pi/extensions/thinking-budget` sums `thinking_delta` characters and, past
// ceil(chars / 3.5) > 4096 (~14,337 chars), turns thinking off, queues "[thinking budget exceeded]
// Please commit..." and calls ctx.abort(); pi then drops the aborted message from every later
// request, so the thought is lost. The operator's decision (ao-thinkon, after ao-think STOPPED):
// no client-side cap and no client steer - the server's reasoning budget (llama-swap
// --reasoning-budget) is the only bound on a thought.
//
// 1. Upstream's `thinking-budget` dir is MOVED out of the dir the launcher discovers extensions in
//    (`<npm root>/little-coder/.pi/extensions/<name>/index.ts`, bin/little-coder.mjs step 4) to the
//    park dir: kept for provenance, never loaded.
// 2. Fail closed. The build fails if upstream's extension is missing or no longer holds the abort
//    (renamed or rewritten upstream: re-read it and adapt this deliberately), or if ANY file left in
//    the discovered set - or in our own extension dirs, which the entrypoint copies into it - reads
//    thinking (THINKING_READER below) or still holds the cap's abort. No extension may read or abort
//    on thinking; this is the seam that keeps that true across upstream syncs.
//
// Not scanned (documented, findings ao-thinkon): LITTLE_CODER_EXTRA_EXTENSIONS (unset by us) and the
// user extensions dir (pinned to the empty, root-owned /opt/little-coder/user-extensions); a reader
// that reaches thinking without any of the THINKING_READER spellings would pass - re-read upstream's
// extensions on every version bump.
import { execSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const UPSTREAM = "thinking-budget";
const UPSTREAM_ABORT = "thinking budget exceeded";
// pi's thinking stream events and content blocks, and the OpenAI-compatible reasoning field.
export const THINKING_READER = /thinking_(delta|start|end)\b|type\s*[!=]==?\s*["']thinking["']|\.thinking\b|reasoning_content/;

function fail(msg) {
  console.error(`[remove-thinking-cap] ${msg} - failing the build`);
  process.exit(1);
}

function* codeFiles(dir) {
  for (const name of readdirSync(dir).sort()) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === "node_modules") continue;
      yield* codeFiles(p);
    } else if (/\.(ts|mts|cts|js|mjs|cjs)$/.test(name) && !/\.test\./.test(name)) {
      yield p;
    }
  }
}

const [park, ...ours] = process.argv.slice(2);
if (!park) fail("usage: remove-thinking-cap.mjs <park dir> [<our extension dir> ...]");

// REMOVE_THINKING_CAP_NPM_ROOT: test seam only (tests/test_thinking_cap.py runs this against a fake
// package tree); the image build never sets it.
const globalRoot = process.env.REMOVE_THINKING_CAP_NPM_ROOT || execSync("npm root -g", { encoding: "utf8" }).trim();
const extDir = join(globalRoot, "little-coder", ".pi", "extensions");
if (!existsSync(extDir)) fail(`no bundled extension dir at ${extDir} - upstream layout changed`);

const upstreamIndex = join(extDir, UPSTREAM, "index.ts");
if (!existsSync(upstreamIndex)) {
  fail(`upstream ${UPSTREAM}/index.ts not found - renamed or removed upstream; re-check what caps thinking now`);
}
if (!readFileSync(upstreamIndex, "utf8").toLowerCase().includes(UPSTREAM_ABORT)) {
  fail(`upstream ${UPSTREAM} no longer holds "${UPSTREAM_ABORT}" - it changed; re-read it before removing it`);
}

mkdirSync(park, { recursive: true });
cpSync(join(extDir, UPSTREAM), join(park, UPSTREAM), { recursive: true });
rmSync(join(extDir, UPSTREAM), { recursive: true, force: true });
if (existsSync(join(extDir, UPSTREAM))) fail(`${UPSTREAM} is still in ${extDir}`);

// Verify: nothing that pi will load reads thinking or holds the cap's abort.
for (const root of [extDir, ...ours]) {
  if (!existsSync(root)) fail(`scan dir ${root} does not exist`);
  for (const f of codeFiles(root)) {
    const text = readFileSync(f, "utf8");
    const where = `${root === extDir ? "bundled" : root}:${relative(root, f).replaceAll("\\", "/")}`;
    if (text.toLowerCase().includes(UPSTREAM_ABORT)) fail(`${where} still holds the length-cap abort`);
    const m = text.match(THINKING_READER);
    if (m) fail(`${where} reads thinking ("${m[0]}") - no extension may read or abort on thinking`);
  }
}

const discovered = readdirSync(extDir).filter((n) => existsSync(join(extDir, n, "index.ts"))).sort();
console.log(`[remove-thinking-cap] ${UPSTREAM} parked in ${park}; no thinking reader in the discovered set ` +
  `(${discovered.length}: ${discovered.join(",")})${ours.length ? ` or in ${ours.join(",")}` : ""}`);
