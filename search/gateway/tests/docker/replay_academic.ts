// rw-p5a-intake: the 2026-09-11 audit query replayed with the academic contract class (run_scholarly_replay.py
// runs this in the research service's own image, on the lab network, against the LAB gateway).
//
// The research service's own enforcement point does the work: resolveContract({classes: ["academic"]}) and
// withContract(deps) - the same two calls runResearch makes - over a searchWeb that asks the lab gateway's
// GET /search exactly as index.ts searchWeb does (q, format=json and, when the contract routes the search,
// categories). It records, per query: the categories sent, every raw hit with the engine(s) that answered it,
// and the hits the contract kept. Run with the research image's /app modules (the base image for RED).
//   env GW (the lab gateway), QUERIES (JSON list), CLASS (default academic), MODE (contract | none)
import { newContractDrops, resolveContract } from "/app/contract.ts";
import { type Deps, withContract } from "/app/harness.ts";

const GW = (Deno.env.get("GW") ?? "").replace(/\/+$/, "");
const QUERIES = JSON.parse(Deno.env.get("QUERIES") ?? "[]") as string[];
const MODE = Deno.env.get("MODE") ?? "contract";
const K = 50;

type Raw = { url: string; title: string; engine?: string; engines?: string[] };
const out: Record<string, unknown>[] = [];
for (const q of QUERIES) {
  let sent: string[] | undefined;
  const raw: Raw[] = [];
  const searchWeb = async (query: string, k: number, opts?: { categories?: string[] }) => {
    sent = opts?.categories;
    const cats = (opts?.categories ?? []).filter(Boolean);
    const u = `${GW}/search?q=${encodeURIComponent(query)}&format=json` + (cats.length ? `&categories=${encodeURIComponent(cats.join(","))}` : "");
    const r = await fetch(u, { headers: { accept: "application/json" } });
    const d = await r.json().catch(() => ({}));
    const results = Array.isArray(d?.results) ? d.results : [];
    for (const x of results) raw.push({ url: String(x.url ?? ""), title: String(x.title ?? ""), engine: x.engine, engines: x.engines });
    return results.slice(0, k).map((x: Record<string, unknown>) => ({ url: String(x.url || ""), title: String(x.title || ""), snippet: String(x.content || "") }))
      .filter((h: { url: string }) => h.url);
  };
  const base = { searchWeb, fetchPage: () => Promise.resolve({ page: null, outcome: "error" }) } as unknown as Deps;
  const rc = MODE === "contract" ? resolveContract({ contract: { sources: { classes: [Deno.env.get("CLASS") ?? "academic"] } } }) : null;
  const deps = rc ? withContract(base, rc, newContractDrops()) : base;
  const kept = await deps.searchWeb(q, K);
  out.push({ query: q, mode: MODE, categories_sent: sent ?? null, raw_hits: raw.length, raw, kept: kept.map((h) => h.url) });
}
console.log("REPLAY " + JSON.stringify(out));
