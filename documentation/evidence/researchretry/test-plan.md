# Test plan - researchretry

Anchor: `queue.ps1 -Show -Id researchretry` (confirmed 2026-09-06). Branch
`work/researchretry`, worktree `wt-researchretry`, base `e72c678`. Parent
commit `dfac66c` (the gitlink bump). **OB1 commit
`d0c8a65abc8ec4a4e98ae15aa65a5635ce795656`** on OB1 branch
`fix/research-curator-call-timeout-retry`, cut from the line's pin `d89c126`.

**You are testing two claims:**

1. `delegateToCurator` can no longer hang forever and no longer fails a run on
   a single connection-level blip: every attempt carries
   `AbortSignal.timeout(CURATOR_TIMEOUT_MS)`, connection-level failures and
   timeouts are retried up to `CURATOR_RETRIES` attempts in total with a
   bounded backoff, and an HTTP answer of any status is NEVER retried (T1, T3).
2. Nothing downstream changed: `harness.ts:686` still swallows the throw into
   `curator.error`, `classifyCuratorOutcome` is byte-identical, and the string
   that reaches `research_jobs.error` still begins with the curator2 prefix and
   now names the attempt count and the last cause (T4, T5).

The policy is pure and injectable (`lib.ts:383-531` at the pin), so T1 needs no
network. T3 runs the IMAGE built from the pin against throwaway containers on
the default bridge; nothing here touches `openbrain-research`,
`openbrain-curator` or any `ai-stack_*` network, and nothing deploys.

**A case you cannot execute is a plan inadequacy, not a scoped pass.** Use
`queue.ps1 -PlanInadequate` naming the case; never write `PASS (scoped)`,
`SKIPPED` or a pass on partial evidence. T7 is the check on that.

**A case that prints a secret is itself a FAIL.** Every live case below uses
the throwaway key `rr-test-key` (or `wrong`) and never the stack's
`MCP_ACCESS_KEY`. If your evidence contains a real `*_KEY` or `PASSWORD` value
the case is a FAIL regardless of what else it showed.

**Semantics you are checking against** (`lib.ts:383-416` comment block):
`retries` is the TOTAL number of attempts (`CURATOR_RETRIES=3` = one call + two
retries); backoff is 2 s, 4 s, 8 s, capped at 10 s, no jitter; worst case with
defaults (`3 x 15000 + 2000 + 4000`) is **51 000 ms**; every live case below
uses `CURATOR_TIMEOUT_MS=5000` so its bound is **21 000 ms**.

## Environment

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-researchretry"
    git rev-parse HEAD                  # dfac66c1b1cafa0e8f2151189f6f776ef05b211b or later on work/researchretry
    git -C OB1 rev-parse HEAD           # d0c8a65abc8ec4a4e98ae15aa65a5635ce795656
    deno --version                      # 2.x on PATH (developer: 2.8.1)
    docker version --format '{{.Server.Version}}'

No plane lease: T1 is in-process, T2 builds a throwaway tag the gate removes,
T3 builds `openbrain-research:wt-researchretry` and runs containers on the
default `bridge` only. Never tag `:local`, never `--network ai-stack_*`.
`<S>` below is any scratch directory of yours; the driver script it needs is
reproduced in T3.

---

## T0 - the gitlink is reachable (curator2's rule). Do this FIRST.

    git -C OB1 ls-remote origin refs/heads/fix/research-curator-call-timeout-retry
    git -C OB1 rev-parse HEAD
    git ls-tree HEAD OB1
    git -C OB1 status --short | Measure-Object     # Count 0

All three SHAs must be `d0c8a65abc8ec4a4e98ae15aa65a5635ce795656`. If
ls-remote does not show it, **FAIL the item immediately** - a fresh
`--recurse-submodules` clone breaks. (Developer's run: all three printed
`d0c8a65abc8ec4a4e98ae15aa65a5635ce795656`, dirty count 0.)

## T1 - unit suite + two mutations (acceptance checks #1, #2, #4)

Acceptance #1, the static grep:

    grep -nE 'AbortSignal.timeout|CURATOR_TIMEOUT_MS|CURATOR_RETRIES' OB1/integrations/research-service/index.ts

Expect at least: `:107 const CURATOR_TIMEOUT_MS = ...`, `:108 const
CURATOR_RETRIES = ...`, `:370 retries: CURATOR_RETRIES,`, `:371 timeoutMs:
CURATOR_TIMEOUT_MS,`. The `AbortSignal.timeout` for the curator call is built
INSIDE the wrapper (`lib.ts:511`) so the tests can see it; `index.ts:361`
says so in a comment.

The suite:

    cd OB1/integrations/research-service
    deno test --allow-env lib.test.ts

Expect `ok | 33 passed | 0 failed` (25 at `d89c126` + 8 new). The 8 new
cases, all driving `delegateCuratorWithRetry` with an INJECTED fetch and an
injected sleep (`lib.test.ts:248-399`):

- connection refused retried 3 times then surfaces with `after 3 attempt(s)`
  and `Connection refused` in the message; sleeps `[2000, 4000]`; 2 log lines
- a per-attempt `TimeoutError` is retried (fake fetch resolves ONLY via the
  signal; without one it refuses instead of hanging the suite)
- HTTP 500 JSON answer: 1 call, no sleep, message `curator 500: {...}`; HTTP
  400 likewise
- HTTP 200: returned on the first attempt, `init.signal instanceof AbortSignal`
- a refusal that clears on attempt 2 succeeds (2 calls, one 2000 ms sleep)
- a `SyntaxError` surfaces unchanged after 1 call (neither transport nor timeout)
- `shouldRetryCuratorError` table (ECONNREFUSED/ECONNRESET/EPIPE/EHOSTUNREACH/
  EAI_AGAIN/"error sending request"/"connection reset"/"timed out" and
  TimeoutError/AbortError -> true; `CuratorHttpError(503)`, `Error("curator
  502: ...")`, `Error("boom")`, SyntaxError, null, undefined -> false)
- backoff `2000, 4000, 8000, 10000, 10000`, `curatorWorstCaseMs(3, 15000) ==
  51000`, `curatorWorstCaseMs(1, x) == x`

**Mutation A - remove the retry.** Make the wrapper throw on every failure:

    Copy-Item lib.ts lib.ts.orig
    (Get-Content -Raw lib.ts) -replace 'if \(!shouldRetryCuratorError\(e\)\) throw e;', 'throw e;' | Set-Content -NoNewline lib.ts
    deno test --allow-env lib.test.ts
    Copy-Item lib.ts.orig lib.ts -Force; Remove-Item lib.ts.orig
    deno test --allow-env lib.test.ts

Expect the mutated run to print `FAILED | 30 passed | 3 failed`, the three
being *connection refused is retried...*, *a per-attempt timeout ... is
retried* and *a refusal that clears on the second attempt succeeds*; the
restored run prints `ok | 33 passed | 0 failed`. (Developer's run: exactly
that.) If the mutated run is green, the refused test is not testing the retry
- FAIL.

**Mutation B - remove the signal.**

    Copy-Item lib.ts lib.ts.orig
    (Get-Content -Raw lib.ts) -replace '\{ \.\.\.init, signal: AbortSignal\.timeout\(opts\.timeoutMs\) \}', '{ ...init }' | Set-Content -NoNewline lib.ts
    deno test --allow-env lib.test.ts
    Copy-Item lib.ts.orig lib.ts -Force; Remove-Item lib.ts.orig
    deno test --allow-env lib.test.ts

Expect `FAILED | 31 passed | 2 failed` - *a per-attempt timeout ... is
retried* (message `the timed-out attempt was retried once`) and *HTTP 200
returns ... first attempt* (`every attempt carries a signal`); restored `ok |
33 passed`. (Developer's run: exactly that.) The fake fetch in the timeout
case rejects with `fake fetch: no AbortSignal supplied` rather than hanging,
which is why the suite finishes in milliseconds under this mutation.

After both: `git -C OB1 status --short` prints nothing (lib.ts restored
byte-for-byte; the developer confirmed with `cmp`).

Type check: `deno check index.ts` prints `Check index.ts` and exits 0.

## T2 - gate 5d GREEN on this pin, RED on the incident pin (by hand)

    powershell -NoProfile -File scripts\checks\check-ob1-integration-images.ps1 -OldPin d89c126 -NewPin d0c8a65; echo $LASTEXITCODE

Expect exit `0` and, in order: `touches image-bearing integration(s):
research-service`; `research-service/Dockerfile copies by glob/directory ...
static half skipped`; `build: ob1-gate/research-service:d0c8a65 built and
'deno check index.ts' resolved inside it (Ns); tag removed`; `OK - 1
integration image(s) build and resolve at OB1 d0c8a65`. (Developer's run:
exit 0, 6907 ms wall, build 4 s.) Docker must be reachable; a REFUSE for a
missing docker is a plan inadequacy on your host, not a pass.

The green is not free - the same gate refuses the pin that shipped the curator
crash loop (the object is in this clone):

    git -C OB1 cat-file -e "48c0363^{commit}"; echo $LASTEXITCODE     # 0
    powershell -NoProfile -File scripts\checks\check-ob1-integration-images.ps1 -OldPin 48c0363 -NewPin a07103b; echo $LASTEXITCODE

Expect exit `1`, a line naming `research-curator/index.ts:39` and `'pool.ts'`,
`FAIL: 1 relative import(s) ...`, `No docker build was attempted`.
(Developer's run: exit 1.) If BOTH runs are green, the gate is broken - FAIL
T2 and say so.

## T3 - live, isolated: stopped, paused and healthy curators, wall-clock quoted

Build from the worktree's OB1 clone (disk == pin after T0):

    cd OB1\integrations\research-service
    docker build --progress=plain --no-cache -t openbrain-research:wt-researchretry . 2>&1 | Select-String -Pattern "RUN deno check|Check\S*\s+\S*file:///app/index.ts|COPY \*\.ts|rm -f|naming to" | ForEach-Object { $_.Line }

Expect `[5/7] COPY *.ts ./`, `[6/7] RUN rm -f ./*.test.ts`, `[7/7] RUN deno
check index.ts`, a `Check ... file:///app/index.ts` line (escape sequences
between the words - see curatorimg T3), `naming to
docker.io/library/openbrain-research:wt-researchretry done`. Without
`--no-cache` the `Check` line is hidden by layer caching (the developer's
first build showed the step, not the line).

The driver. It is mounted INTO the image so it imports the image's own
`lib.ts` (the code under test) and calls `delegateCuratorWithRetry` with the
exact init and options `index.ts:364-374` passes; no LLM, no DB, no research
run. Save as `<S>\drive-curator.ts`:

```ts
import { classifyCuratorOutcome, curatorWorstCaseMs, delegateCuratorWithRetry } from "./lib.ts";
const env = (k: string, d = "") => Deno.env.get(k) ?? d;
const CURATOR_URL = env("CURATOR_URL", "http://openbrain-curator:8000").replace(/\/+$/, "");
const FETCH_TIMEOUT_MS = parseInt(env("FETCH_TIMEOUT_MS", "15000"), 10);
const CURATOR_TIMEOUT_MS = parseInt(env("CURATOR_TIMEOUT_MS", String(FETCH_TIMEOUT_MS)), 10) || FETCH_TIMEOUT_MS;
const CURATOR_RETRIES = Math.max(1, parseInt(env("CURATOR_RETRIES", "3"), 10) || 1);
const pkg = JSON.parse(env("PKG", "{}"));
const bound = curatorWorstCaseMs(CURATOR_RETRIES, CURATOR_TIMEOUT_MS);
console.log(`drive: url=${CURATOR_URL}/ingest/research-package retries=${CURATOR_RETRIES} timeoutMs=${CURATOR_TIMEOUT_MS} worst_case_ms=${bound}`);
const t0 = Date.now();
try {
  const out = await delegateCuratorWithRetry(fetch, `${CURATOR_URL}/ingest/research-package`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-brain-key": env("MCP_ACCESS_KEY", "unset") },
    body: JSON.stringify(pkg),
  }, { retries: CURATOR_RETRIES, timeoutMs: CURATOR_TIMEOUT_MS, log: (line) => console.warn(`[${Date.now() - t0} ms] ${line}`) });
  console.log(`OK after ${Date.now() - t0} ms: ${JSON.stringify(out).slice(0, 200)}`);
} catch (e) {
  const ms = Date.now() - t0;
  const message = String((e as Error).message);           // harness.ts:686
  console.log(`ERROR after ${ms} ms (worst_case_ms=${bound}, within_bound=${ms <= bound + 1000}): ${message}`);
  console.log(`research_jobs.error would be: ${classifyCuratorOutcome({ error: message }).error}`);
  Deno.exit(2);
}
```

Containers, default bridge only (`openbrain-curator:wt-curatorimg` exists
locally from item curatorimg; a dead `DB_HOST` keeps the process up and
answering, its `/health` is 503 - that is curatorimg T3's documented state):

    docker run -d --name rr-curator -e DB_HOST=127.0.0.1 -e DB_PASSWORD=x -e MCP_ACCESS_KEY=rr-test-key openbrain-curator:wt-curatorimg
    docker run -d --name rr-stub denoland/deno:2.3.3 eval "Deno.serve({ port: 8000 }, () => Response.json({ thread_id: 'stub-thread', sources_written: 0, stub: true }))"
    docker inspect -f '{{.NetworkSettings.Networks.bridge.IPAddress}} {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' rr-curator rr-stub

Expect each to print an IP and exactly `bridge`. Substitute `<CUR>` and
`<STUB>` below (developer: 172.17.0.2 / 172.17.0.3). One run helper (PowerShell;
in Git Bash prefix `MSYS_NO_PATHCONV=1` so the mount paths survive):

    $img = "openbrain-research:wt-researchretry"
    $D = "-v <S>\drive-curator.ts:/app/drive-curator.ts:ro"
    $run = "run --allow-net --allow-env --allow-read=/app drive-curator.ts"
    # every case: measure the host wall clock around the docker run as well

**(a) curator STOPPED - error within the bound, N attempts logged.**

    docker stop -t 2 rr-curator
    docker run --rm $D -e CURATOR_URL=http://<CUR>:8000 -e CURATOR_TIMEOUT_MS=5000 -e CURATOR_RETRIES=3 -e MCP_ACCESS_KEY=rr-test-key $img $run
    docker start rr-curator

Expect exit `2`; two lines `curator attempt 1/3 failed: TypeError: error
sending request ... Connection refused (os error 111) ...; retrying in 2000
ms` and `attempt 2/3 ... retrying in 4000 ms` (timestamps ~0 ms and ~2000
ms); `ERROR after ~6000 ms (worst_case_ms=21000, within_bound=true): curator
unreachable after 3 attempt(s) (timeout 5000 ms each): TypeError: ...
Connection refused`. On Docker Desktop's bridge a stopped container's former
IP answers with a RESET, so the error is `Connection refused`, not
`EHOSTUNREACH`; either is a connection-level error and both are retried.
(Developer's run: `ERROR after 6010 ms`, host wall 6878 ms.) Also run the
same against a RUNNING curator on a port nothing listens on
(`CURATOR_URL=http://<CUR>:8001`) - same shape (developer: 6010 ms).

**(b) curator PAUSED longer than the timeout - each attempt times out.**

    docker pause rr-curator
    docker run --rm $D -e CURATOR_URL=http://<CUR>:8000 -e CURATOR_TIMEOUT_MS=5000 -e CURATOR_RETRIES=3 -e MCP_ACCESS_KEY=rr-test-key $img $run
    docker unpause rr-curator

Expect exit `2`; `[~5003 ms] curator attempt 1/3 failed: TimeoutError:
Signal timed out. (the curator may already have received the package; its
persist path dedupes); retrying in 2000 ms`; `[~12005 ms] ... attempt 2/3
... retrying in 4000 ms`; `ERROR after ~21007 ms (worst_case_ms=21000,
within_bound=true): curator unreachable after 3 attempt(s) (timeout 5000 ms
each): TimeoutError: Signal timed out.` The wall clock is the bound itself:
3 x 5000 + 2000 + 4000. (Developer's run: 21007 ms, host wall 21926 ms; a
second run 21009 ms.)

**(c) healthy curator - first try, no retry.** A 2xx from the REAL curator
needs an embedder, a Postgres with the OB schema and a persist delegate
(`research-curator/index.ts:543-551`), which an isolated bridge container
cannot have. So the healthy case is two halves, both first-try:

(c1) the stub answers 200:

    docker run --rm $D -e CURATOR_URL=http://<STUB>:8000 -e CURATOR_TIMEOUT_MS=5000 -e CURATOR_RETRIES=3 -e MCP_ACCESS_KEY=rr-test-key $img $run

Expect exit `0`, no `attempt` lines, `OK after <~10> ms:
{"thread_id":"stub-thread","sources_written":0,"stub":true}`. (Developer: 8 ms.)

(c2) the real isolated curator, process healthy, answers a 400 verdict for an
empty package - returned on the first attempt, NOT retried:

    docker run --rm $D -e CURATOR_URL=http://<CUR>:8000 -e CURATOR_TIMEOUT_MS=5000 -e CURATOR_RETRIES=3 -e MCP_ACCESS_KEY=rr-test-key $img $run
    docker run --rm $D -e CURATOR_URL=http://<CUR>:8000 -e CURATOR_TIMEOUT_MS=5000 -e CURATOR_RETRIES=3 -e MCP_ACCESS_KEY=wrong $img $run

Expect exit `2` both times, NO `attempt` lines, `ERROR after <~10> ms ...:
curator 400: {"error":"claim required"}` and `... curator 401:
{"error":"unauthorized"}` respectively. An `attempt 1/3` line here is a FAIL:
an HTTP answer was retried. (Developer: 9 ms and 6 ms.)

**(d) live mutation - the acceptance's own wording.** Mount a mutated
`lib.ts` OVER the image's (`-v <S>\lib.mut.ts:/app/lib.ts:ro`), using the two
`-replace` expressions from T1:

- signal removed, curator PAUSED: start the run with `docker run -d --name
  rr-mut ...`, `Start-Sleep 35`, then `docker inspect -f '{{.State.Status}}'
  rr-mut` must print `running` (hung past the 21 000 ms bound) and `docker
  logs rr-mut` must show only the `drive:` line. Then `docker kill rr-mut;
  docker container rm rr-mut; docker unpause rr-curator`. (Developer: still
  `running` at 36 187 ms, one log line.) Run the unmutated image again
  against the paused curator - it must time out at ~21 000 ms.
- retry removed, refused port (`<CUR>:8001`): exit `2`, NO `attempt` lines,
  `ERROR after <~5> ms ...: error sending request ... Connection refused` -
  the raw error, no `after N attempt(s)`. (Developer: 4 ms, host wall 1236
  ms.) Then the unmutated image: three attempts, `after 3 attempt(s)`.

Cleanup: `docker stop rr-curator rr-stub; docker container rm rr-curator
rr-stub`. Keep the `:wt-researchretry` tag.

## T4 - the string that reaches research_jobs.error

Every failing run above printed a final line `research_jobs.error would be:
...`, computed the way `harness.ts:686` + `classifyCuratorOutcome`
(`lib.ts:332`) do it. Check it in (a) and (b):

- begins with `curator: the research completed but was NOT filed into Open
  Brain - ` (the curator2 prefix, unchanged);
- continues `curator unreachable after 3 attempt(s) (timeout 5000 ms each):
  ` and then the last cause (`TypeError: ... Connection refused` in (a),
  `TimeoutError: Signal timed out.` in (b)).

And in (c2) it is `... - curator 400: {"error":"claim required"}` - a verdict
keeps its old shape. The unit case *curator failure => status error* still
covers the prefix; nothing in `classifyCuratorOutcome` moved (T5).

## T5 - harness.ts:686 and classifyCuratorOutcome are unchanged

    git -C OB1 diff --stat d89c126 d0c8a65 -- integrations/research-service/harness.ts
    git -C OB1 show d0c8a65:integrations/research-service/harness.ts | Select-Object -Index 685
    git -C OB1 diff d89c126 d0c8a65 -- integrations/research-service/lib.ts | Select-String -Pattern '^-[^-]' | Measure-Object
    git -C OB1 diff --stat d89c126 d0c8a65

Expect: the harness diff is EMPTY; line 686 reads exactly `    try { curator
= await deps.delegateToCurator(pkg); } catch (e) { curator = { error:
String((e as Error).message) }; }`; the lib.ts diff has `Count 0` removed
lines (the change is append-only, so `classifyCuratorOutcome` at `:332-359`
is byte-identical); the stat names exactly `index.ts`, `lib.test.ts`,
`lib.ts` under `integrations/research-service/` and nothing else
(`3 files changed, 322 insertions(+), 5 deletions(-)`; the 5 deletions are
the old `delegateToCurator` body in index.ts).

## T6 - the gitlink is an integration with the line's pin, not just an ancestor

    $linePin = (git ls-tree refactor/ai-stack-cleanup OB1).Split()[2]
    $pin     = (git ls-tree HEAD OB1).Split()[2]
    git -C OB1 fetch origin
    git -C OB1 merge-tree --write-tree $linePin $pin
    git -C OB1 rev-parse "$pin^{tree}"

Expect the two hashes IDENTICAL. Today `$linePin` is `d89c126` and `$pin` is
`d0c8a65`, so the merge-tree is trivially `d0c8a65^{tree}` (developer's run:
both printed `35127cc2161aae60a4ec121febe282eca3052a77`). If the line's pin
has moved by review, the reviewer's integration commit becomes `$pin`, this
case is re-run against it, and an earlier pass is stale. Conflict markers
from `merge-tree` are a FAIL, not something to resolve in the tester's seat.

## T7 - every case above carries a bare PASS on its heading line

    (Select-String -Path <evidence.md> -Pattern "^## T\d+.*\bPASS\b").Count

Expect `8` (T0-T7). A case with FAIL, SKIPPED, scoped or no verdict is a plan
inadequacy or a fail; it is never rounded up. And the secret rule applies to
the evidence as a whole: `Select-String -Path <evidence.md> -Pattern
"PASSWORD=|_KEY="` must match only `DB_PASSWORD=x`, `MCP_ACCESS_KEY=rr-test-key`
and `MCP_ACCESS_KEY=wrong` from T3, nothing else.
