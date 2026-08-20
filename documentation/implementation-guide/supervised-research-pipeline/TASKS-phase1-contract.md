# TASKS — Phase 1: Per-job research contract (policy-as-data)

> Status: BUILD SPEC 2026-08-05 — not started. Design-of-record:
> `PLAN-supervised-research-pipeline.md` §"Phase 1". Anchors below verified
> against the live service (`OB1/integrations/research-service/`, image
> `openbrain-research:local`) on 2026-08-05.

## Architectural insight (why this is low-risk)

`runResearch(deps, …)` reaches the network **only** through `deps.searchWeb` /
`deps.fetchPage` (`harness.ts:62-63`, called at `harness.ts:334,347,448,451`).
`executeJob` already builds a **per-job `jobDeps` wrapper** over `realDeps`
(`index.ts:401-403`, today wrapping `chat` for queue attribution). Source
allow/deny is therefore enforced by **wrapping `searchWeb`/`fetchPage` at that one
site — zero edits to the gather loop.** Budget narrowing reuses the existing pure
`backstopDecision` (`lib.ts:117`) by clamping the ceilings `runResearch` already
computes. `domainOf(url)` (`lib.ts:47`) is the matcher input.

**No schema migration.** The contract rides inside `research_jobs.options`
(jsonb, `init-research-jobs.sql:37`) and is echoed into `result` (jsonb). The POST
handler already spreads `body.options` into the stored options
(`index.ts:605-606`), so a caller placing `contract` under `options.contract`
persists it with **no handler change**. Nothing in `init-*.sql` changes.

## Contract shape

```ts
// contract.ts  (NEW)
export interface ResearchContract {
  sources?: {
    allow?: string[];   // domain globs; non-empty ⇒ default-deny outside the set
    deny?:  string[];   // domain globs; deny always wins over allow
    classes?: string[]; // named sets from the registry, expanded into allow[]
  };
  budget?: {
    max_fetch?: number; // clamps DOWN only (min with MAX_FETCH env ceiling)
    wall_ms?:   number; // clamps DOWN only (min with MAX_WALL_MS)
    rounds?:    number; // clamps DOWN only (min with MAX_ROUNDS)
  };
  redlines?: Array<"no_exploit_fetch">;  // hard denials; extensible enum
}
```

Example (the outline's Linux-advisories use case):
```json
{ "options": { "contract": {
  "sources": { "classes": ["advisories"] },
  "redlines": ["no_exploit_fetch"],
  "budget": { "max_fetch": 20 }
}}}
```

## Enforcement semantics (specify exactly, then test)

- **Matching:** `matchDomain(host, glob)` on `domainOf(url)`. `*.nist.gov` matches
  `nvd.nist.gov` and `nist.gov`; bare `nist.gov` matches host-or-subdomain. Case-fold.
- **Precedence:** `deny` beats `allow`. Empty/absent `allow` ⇒ permit-all
  (status quo). Non-empty `allow` ⇒ permit **only** listed (default-deny within).
- **Scope of the allow-list:** applies to **discovered** sources only
  (`searchWeb` hits + `fetchPage` URLs). **Caller `seedSources` are exempt from
  `allow`** (in article mode the seed IS the subject — `harness.ts:254-260`) but
  are still checked against `deny` + red lines. State this in code comments.
- **`no_exploit_fetch` red line** = (a) deny a built-in exploit/PoC host set
  (exploit-db.com, packetstorm, `*/poc`, etc.) merged into `deny`, AND (b) a
  query guard that drops any generated `searchWeb`/deepen query containing
  exploitation-operationalizing terms (regex, conservative) before it is issued.
- **Budget:** contract can only **narrow**. `maxSources = min(MAX_FETCH,
  contract.budget.max_fetch ?? ∞)`, same for wall/rounds. A contract can never
  raise a ceiling, widen egress, or disable injection defense.

## Fail-closed invariant

`resolveContract(options)` validates and expands. **Malformed contract → the job
errors** (status `error`, clear message) rather than running wide-open — silently
ignoring a red line is the dangerous failure. Unknown `classes`/`redlines` value =
malformed. A job with **no** `contract` key runs exactly as today.

## Tasks

- **T1 — `contract.ts` (NEW).** `ResearchContract` type; `resolveContract(options)`
  → `{ allow, deny, redlineQueryGuard, budgetCaps }` or throws
  `ContractError` (fail-closed); `matchDomain`; `CLASS_REGISTRY`
  (`advisories` / `academic` / `vendor-psirt` → domain glob lists) as an in-repo
  const map. Pure module, fully unit-testable, no I/O.
- **T2 — `RunOptions.contract` + `runResearch` (`harness.ts`).** Add
  `contract?: ResolvedContract` to `RunOptions` (`harness.ts:156`). In
  `runResearch`: clamp the ceilings fed to `backstopDecision` (`harness.ts:326`)
  by `contract.budgetCaps`; apply `redlineQueryGuard` to `searchWeb`/deepen
  queries before issuing (`harness.ts:334,448` and the DEEPEN query build).
- **T3 — deps wrap in `executeJob` (`index.ts:401-403`).** After queue-user wrap,
  compose contract enforcement:
  `searchWeb: (q,k) => realSearchWeb(q,k).then(hits => hits.filter(h => contract.permits(h.url)))`
  and `fetchPage: (u) => contract.permits(u) ? realFetchPage(u) : {page:null, outcome:"error"}`.
  Call `resolveContract(options)` at the top of the `try` (fail-closed → the
  existing `catch` writes status `error`). Pass `contract` into the `runResearch`
  opts object (`index.ts:404-413`).
- **T4 — echo into `result` (`index.ts:417-423`).** Add
  `contract: resolvedContract` to the result jsonb so the trace records *what the
  job was allowed to do* (the outline's "no traces, no trust" applied to scope).
- **T5 — seed handling.** In `runResearch` seed staging (`harness.ts:258`), drop
  any seed whose domain hits `deny`/red lines (allow-list exempt, per semantics).
- **T6 — tests (`contract.test.ts`, extend `lib.test.ts`).** matcher globs;
  deny-beats-allow; empty-allow permits all; budget clamps only downward, never
  up; unknown class/redline ⇒ throw; `no_exploit_fetch` drops an exploit query +
  denies an exploit host; a no-contract job is byte-identical to today.
- **T7 — registry seed + deploy.** Populate `CLASS_REGISTRY.advisories`
  (nvd.nist.gov, cve.mitre.org, `*.cert.org`, cisa.gov, vendor PSIRT hosts).
  Note the class list is intentionally small/reviewed, not exhaustive.

## Deploy ladder (built image, NOT bind-mounted)

Unlike the digest recipe, `openbrain-research` runs a built image
(`openbrain-research:local`), so:
1. `deno check` + `deno lint` clean on touched files.
2. `deno test` (T6) green.
3. `docker compose build openbrain-research`
4. `docker compose up -d openbrain-research` (recreate; drain loop resumes,
   `recoverOrphanedJobs()` re-queues any in-flight job).
5. Dry-run verify: submit a job with `contract.sources.classes=["advisories"]` +
   a query that would normally pull a blog; confirm `result.contract` echoes and
   non-advisory hits are absent from `cited_sources`.
No `emergency-recovery` / `stack-map` change (no container/network/port change).

## Interplay with later phases (out of scope here)

- **Phase 2 (Skeptic)** is **independent** of the contract (its self-heal loop
  stands on the existing gather + backstop). When a contract is present, it only
  *further* constrains the Skeptic's re-gather (allowed sources / budget) — a
  refinement, not a dependency.
- **Phase 3 (attribution)** fixes `origin` coercion (`index.ts:620`, DB CHECK
  `init-research-jobs.sql:30-31` allows only `owui/agent/notebook/manual` so
  `agent-org-*` collapses to `owui`) — a **schema CHECK + handler** change, kept
  out of Phase 1 to preserve "no migration."

Phase 1 is entirely **local** — source scoping, budgets, red lines. It touches no
routing and no cloud; all inference in this effort is local (the stack wires no
cloud model today). Cloud routing is deferred out of this effort.
