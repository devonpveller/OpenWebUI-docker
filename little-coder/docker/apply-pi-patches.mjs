// Build-time application of little-coder's pi-runtime patches (Dockerfile.agent).
//
// Since little-coder 1.12.0 there is no npm postinstall: the launcher applies
// `scripts/patch-pi.mjs` on every launch, best-effort, and swallows any error.
// In this image the agent runs as the unprivileged `lc` user and the global
// node_modules is root-owned, so that launch-time write fails silently and the
// patches (1.20.0: the multi-line `edit` JSON repair, upstream issue #127) would
// never land. This runs once, as root, during the image build.
//
// Fail closed: every patch upstream ships must end up applied. A patch whose
// target file is MISSING fails the build (pi moved the file, or npm laid the
// nested pi-agent-core copy out elsewhere - either way the repair would be
// absent), and so does a patch whose target exists but was not applied (pi
// changed underneath, so its `find` no longer matches). A version bump cannot
// silently ship without a repair; adapt this script deliberately instead.
import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const globalRoot = execSync("npm root -g", { encoding: "utf8" }).trim();
const lcRoot = join(globalRoot, "little-coder");
const patcher = join(lcRoot, "scripts", "patch-pi.mjs");
if (!existsSync(patcher)) {
  console.error(`[apply-pi-patches] no patcher at ${patcher} - upstream layout changed`);
  process.exit(1);
}

const { applyPiPatches, resolvePiRoot, PATCHES } = await import(pathToFileURL(patcher).href);
const candidates = [
  join(lcRoot, "node_modules", "@earendil-works", "pi-coding-agent"),
  join(globalRoot, "@earendil-works", "pi-coding-agent"),
];
const piRoot = candidates.find((c) => existsSync(join(c, "package.json"))) ?? resolvePiRoot();
if (!piRoot) {
  console.error("[apply-pi-patches] cannot resolve the bundled pi package");
  process.exit(1);
}

applyPiPatches(piRoot);

let applied = 0;
let failed = 0;
for (const p of PATCHES) {
  const file = join(piRoot, p.rel);
  if (!existsSync(file)) {
    failed += 1;
    console.error(`[apply-pi-patches] MISSING TARGET ${p.rel}`);
    continue;
  }
  if (readFileSync(file, "utf8").includes(p.applied)) {
    applied += 1;
    console.log(`[apply-pi-patches] OK ${p.rel}`);
  } else {
    failed += 1;
    console.error(`[apply-pi-patches] NOT APPLIED ${p.rel}`);
  }
}
if (failed > 0 || applied === 0) {
  console.error(`[apply-pi-patches] ${failed} patch(es) not applied, ${applied} applied - failing the build`);
  process.exit(1);
}
console.log(`[apply-pi-patches] ${applied}/${PATCHES.length} patch(es) applied in ${piRoot}`);
