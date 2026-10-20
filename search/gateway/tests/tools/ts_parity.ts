// Run the gate's guard corpus through the TypeScript guard it ports (OB1
// integrations/kubernetes-deployment/ingest-egress.ts urlBlockReason) and print, per URL, whether
// the original refuses it. tests/test_guard_parity.py asserts the Python port refuses every URL the
// original refuses (the port may be stricter: ports, wildcard DNS, .localdomain, documentation and
// other non-global ranges, invalid numeric hosts).
//   deno run --allow-read tests/tools/ts_parity.ts <ingest-egress.ts> <guard_corpus.json>
const [modPath, corpusPath] = Deno.args;
const toUrl = (p: string) =>
  /^[A-Za-z]:[\\/]/.test(p)
    ? encodeURI("file:///" + p.replaceAll("\\", "/"))
    : new URL(p, `file://${Deno.cwd().replaceAll("\\", "/")}/`).href;
const mod = await import(toUrl(modPath));
const corpus = JSON.parse(await Deno.readTextFile(corpusPath));
const out: Record<string, string | null> = {};
for (const r of corpus.urls) out[r.url] = mod.urlBlockReason(r.url);
console.log(JSON.stringify(out));
