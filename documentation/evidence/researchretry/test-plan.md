# Test plan - researchretry (attempt 2)

Anchor: `queue.ps1 -Show -Id researchretry` (confirmed 2026-09-06, amended
the same day: CURATOR_TIMEOUT_MS default 180 000; a timeout is NOT retried;
only connection-level failures retry). Attempt 1 FAILED on the tester's
refutation 2b (a 2xx whose BODY stalled past the timeout resolved `{}` and
read as FILED) and the plan was marked INADEQUATE (it drove no shape where
the connection ends after the package was received or after headers). The
delegated seat decided: the body read sits inside the timeout budget; a 2xx
without a JSON object body is NOT filed; retry ONLY connect-phase failures
(Deno's `client error (Connect)` family) - a `(SendRequest)` close or a
body-read failure fails once saying the package may have been received;
`CURATOR_TIMEOUT_MS <= 0` / non-numeric falls back to the default.

Branch `work/researchretry`, worktree `wt-researchretry`, base `e72c678`.
Parent commit `8554c97` (the attempt-2 gitlink bump; `dfac66c` and `995c0c7`
were the earlier ones). **OB1 commit `20ed84b1656a26ce9ccc54df02c4a3410c56de97`**
on OB1 branch `fix/research-curator-call-timeout-retry`, cut from the line's
pin `d89c126` (three commits: `d0c8a65`, `6197bc7`, `20ed84b`). Note:
`20ed84b`'s own commit message miscounts the mutation kills (it says
"signal removed -> 3 fail; timeouts retryable -> 3 fail"); the counts in T1
below are the measured ones and the parent commit `8554c97` says so.

**You are testing three claims:**

1. `delegateToCurator` can no longer hang forever and no longer fails a run on
   a single connect-phase blip: every attempt carries
   `AbortSignal.timeout(CURATOR_TIMEOUT_MS)` over connect, headers AND body;
   connect-phase failures are retried up to `CURATOR_RETRIES` attempts in
   total with a bounded backoff; a TIMEOUT in any phase fails ONCE, loudly,
   naming the elapsed ms; a failure after the request was sent fails once
   saying the package may have been received; an HTTP answer of any status
   is never retried (T1, T3).
2. "Filed" means the curator said so: a 2xx whose body is not a JSON object
   (empty, truncated, HTML) is NOT filed and reaches `research_jobs.error`
   as `curator answered 200 with no JSON verdict (<n> bytes)` (T1, T3(d)).
3. Nothing downstream changed: `harness.ts:686` still swallows the throw into
   `curator.error`, `classifyCuratorOutcome` is byte-identical, and the string
   that reaches `research_jobs.error` still begins with the curator2 prefix and
   names the attempt count and the last cause (T4, T5).

The policy is pure and injectable (`lib.ts:383-665` at the pin), so T1 needs no
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

**Semantics you are checking against** (`lib.ts:383-446` comment block):
`retries` is the TOTAL number of attempts (`CURATOR_RETRIES=3` = one call + two
retries) and applies to connect-phase failures only; backoff is 2 s, 4 s,
8 s, capped at 10 s, no jitter. Two worst cases, stated separately:

- **refused** (`curatorRefusedWorstCaseMs`, `lib.ts:547`): attempts x the
  time a connect takes to fail + the backoffs = `3 x ~5 ms + 2000 + 4000`
  = **~6 s** at the defaults (the driver below budgets a generous 1 s per
  connect-fail and prints `refused_bound_ms=9000`);
- **timeout** (`curatorTimeoutWorstCaseMs`, `lib.ts:560`): **exactly one
  CURATOR_TIMEOUT_MS** in whichever phase it fires - 180 000 ms at the
  default, 5 000 ms in every live case below. Hard ceiling for any mix: the
  backoffs + (attempts-1) connect-fails + one timeout.

Error classes and the message each produces (`lib.ts:464-507`):
`CuratorHttpError` `curator <status>: <json|{}>`; `CuratorTimeoutError`
`curator timed out after <n> ms[ while reading its answer] (CURATOR_TIMEOUT_MS=<t>,
attempt <i>/<N>, not retried - the curator may still be working on the package)`;
`CuratorAfterSendError` `curator connection failed after the request was sent
(attempt <i>/<N>, not retried - the package may have been received): <cause>`;
`CuratorNoVerdictError` `curator answered <status> with no JSON verdict (<n> bytes)`;
and after the last connect-phase failure a plain `Error` `curator unreachable
after <N> attempt(s) in <n> ms (connect-phase failures only, timeout <t> ms per
attempt): <last cause>`.

## Environment

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-researchretry"
    git rev-parse HEAD                  # 8554c97be2ee1b53cdae1a31a46f86b23216ffd0 or later on work/researchretry
    git -C OB1 rev-parse HEAD           # 20ed84b1656a26ce9ccc54df02c4a3410c56de97
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

All three SHAs must be `20ed84b1656a26ce9ccc54df02c4a3410c56de97`. If
ls-remote does not show it, **FAIL the item immediately** - a fresh
`--recurse-submodules` clone breaks. (Developer's run: all three printed
`20ed84b1656a26ce9ccc54df02c4a3410c56de97`, dirty count 0.)

## T1 - unit suite + four mutations (acceptance checks #1, #2, #4)

Acceptance #1, the static grep:

    grep -nE 'AbortSignal.timeout|CURATOR_TIMEOUT_MS|CURATOR_RETRIES' OB1/integrations/research-service/index.ts

Expect at least: `:115 const CURATOR_TIMEOUT_MS = curatorTimeoutMsFromEnv(Deno.env.get("CURATOR_TIMEOUT_MS"));`,
`:116 const CURATOR_RETRIES = curatorRetriesFromEnv(...)`, `:379 retries:
CURATOR_RETRIES,`, `:380 timeoutMs: CURATOR_TIMEOUT_MS,`. The
`AbortSignal.timeout` for the curator call is built INSIDE the wrapper
(`lib.ts:625`) so the tests can see it; `index.ts:369-372` says so. The
default (`lib.ts:448`) is 180 000, NOT `FETCH_TIMEOUT_MS`.

The suite:

    cd OB1/integrations/research-service
    deno test --allow-env lib.test.ts

Expect `ok | 38 passed | 0 failed` (25 at `d89c126` + 13 new). The new cases
all drive `delegateCuratorWithRetry` with an INJECTED fetch, sleep and clock
(`lib.test.ts:249-511`):

- `(Connect)` refused retried 3 times then surfaces with `after 3 attempt(s)
  in 6015 ms`; sleeps `[2000, 4000]`; 2 log lines
- a timeout before any response: 1 call, no sleep, no log,
  `err.name == "CuratorTimeoutError"`, message exactly `curator timed out
  after 20 ms (CURATOR_TIMEOUT_MS=20, attempt 1/3, not retried - the curator
  may still be working on the package)`
- **refutation 2b**: a 200 whose body stream errors only when the wrapper's
  signal fires -> 1 call, `CuratorTimeoutError`, message `... after 25 ms
  while reading its answer ...`, `classifyCuratorOutcome` state `FAILED`
- a 200 with body `""` / `{"thread_id":` / `<html>ok</html>` / `[]` / `null`
  -> 1 call, `CuratorNoVerdictError`, `curator answered 200 with no JSON
  verdict (0|13|15|2|4 bytes)`, state `FAILED`, not retryable
- `(SendRequest)` "connection closed before message completed" -> 1 call,
  `CuratorAfterSendError`, message says `the package may have been received`
  and carries `(SendRequest)`; a body stream erroring with "error reading a
  body" -> same class
- HTTP 500 JSON: 1 call, `curator 500: {...}`; 400 likewise; a plain-text 500
  -> `curator 500: {}` (`CuratorHttpError`, today's shape)
- HTTP 200 JSON object: first attempt, `init.signal instanceof AbortSignal`
- refusal clears on attempt 2 -> success, 2 calls, one 2000 ms sleep
- `SyntaxError` surfaces unchanged after 1 call
- `shouldRetryCuratorError` table: `(Connect)` refused/no-route/reset-by-peer,
  ECONNREFUSED, EHOSTUNREACH, ENETUNREACH, EAI_AGAIN -> true;
  `(SendRequest)`, ECONNRESET, EPIPE, "connection reset by peer", "request
  timed out", "error reading a body", TimeoutError, AbortError, the four
  Curator* classes, `Error("curator 502: ...")`, `Error("boom")`,
  SyntaxError, null, undefined -> false
- env parsing: `curatorTimeoutMsFromEnv("-1"|"0"|"abc"|""|undefined) ==
  180000`, `("5000") == 5000`, `("5000abc") == 5000`;
  `curatorRetriesFromEnv("0"|"-2") == 1`, `("abc"|undefined) == 3`,
  `("2.7") == 2`
- the wrapper handed `timeoutMs: -1` still builds a valid signal and succeeds
- backoff `2000, 4000, 8000, 10000, 10000`; `curatorRefusedWorstCaseMs(3, 5)
  == 6015`; `curatorTimeoutWorstCaseMs(180000) == 180000`

The four mutations. Each: mutate, run, restore, run again; the restored run
must print `ok | 38 passed | 0 failed` and `git -C OB1 status --short` must
be empty afterwards (the developer confirmed with `cmp`). Mutate with
`Copy-Item lib.ts lib.ts.orig`, the `-replace` shown, `Set-Content
-NoNewline lib.ts`; restore with `Copy-Item lib.ts.orig lib.ts -Force`.

**A - remove the retry** (every failure surfaces at once):
`-replace '      last = e;', '      throw e; last = e;'`
Expect `FAILED | 36 passed | 2 failed`: *connection refused (Connect phase)
is retried...* and *a refusal that clears on the second attempt succeeds*.

**B - remove the signal:**
`-replace '\{ \.\.\.init, signal: AbortSignal\.timeout\(timeoutMs\) \}', '{ ...init }'`
Expect `FAILED | 34 passed | 4 failed`: the timeout-before-response case, the
body-stall case, *HTTP 200 ... first attempt* (`every attempt carries a
signal`), and *a non-positive timeoutMs ... falls back*.

**C - make timeouts retryable** (three edits: the gate off, the two names
out of the classifier's refusal list, `timed out` into the connect regex):
`-replace 'if \(isTimeoutLike\(e\)\) \{', 'if (false && isTimeoutLike(e)) {' -replace 'e\.name === "AbortError" \|\| e\.name === "TimeoutError" \|\| e\.name === "CuratorTimeoutError" \|\|', 'e.name === "CuratorTimeoutError" ||' -replace '\|EAI_AGAIN\|getaddrinfo/i;', '|EAI_AGAIN|getaddrinfo|timed out/i;'`
Expect `FAILED | 35 passed | 3 failed`: the timeout-before-response case,
the body-stall case and the `shouldRetryCuratorError` table. A mutation that
only removes the gate is NOT this one - it un-wraps the timeout but never
resends (the developer measured that: one attempt, `name=TimeoutError`).

**D - swallow body-read errors (re-introduce the 2b defect):**
`-replace 'const text = await r\.text\(\);', 'const text = await r.text().catch(() => "{}");'`
Expect `FAILED | 36 passed | 2 failed`: the body-stall case (it would resolve
`{}`) and the `(SendRequest)` case (its body-read half).

(Developer: exactly those four summaries.) Green on any of A-D means the
suite is not testing that claim - FAIL.

Type check: `deno check index.ts` prints `Check index.ts` and exits 0.

## T2 - gate 5d GREEN on this pin, RED on the incident pin (by hand)

    powershell -NoProfile -File scripts\checks\check-ob1-integration-images.ps1 -OldPin d89c126 -NewPin 20ed84b; echo $LASTEXITCODE

Expect exit `0` and, in order: `touches image-bearing integration(s):
research-service`; `research-service/Dockerfile copies by glob/directory ...
static half skipped`; `build: ob1-gate/research-service:20ed84b built and
'deno check index.ts' resolved inside it (Ns); tag removed`; `OK - 1
integration image(s) build and resolve at OB1 20ed84b`. (Developer's run:
exit 0, 6364 ms wall, build 4 s.)

The green is not free - the same gate refuses the pin that shipped the curator
crash loop (the object is in this clone):

    git -C OB1 cat-file -e "48c0363^{commit}"; echo $LASTEXITCODE     # 0
    powershell -NoProfile -File scripts\checks\check-ob1-integration-images.ps1 -OldPin 48c0363 -NewPin a07103b; echo $LASTEXITCODE

Expect exit `1`, a line naming `research-curator/index.ts:39` and `'pool.ts'`,
`FAIL: 1 relative import(s) ...`, `No docker build was attempted`. If BOTH
runs are green, the gate is broken - FAIL T2 and say so.

## T3 - live, isolated: every connection shape, wall-clock quoted

Build from the worktree's OB1 clone (disk == pin after T0):

    cd OB1\integrations\research-service
    docker build --progress=plain --no-cache -t openbrain-research:wt-researchretry . 2>&1 | Select-String -Pattern "RUN deno check|file:///app/index.ts|COPY \*\.ts|rm -f|naming to" | ForEach-Object { $_.Line }

Expect `[5/7] COPY *.ts ./`, `[6/7] RUN rm -f ./*.test.ts`, `[7/7] RUN deno
check index.ts`, `Check file:///app/index.ts`, `naming to
docker.io/library/openbrain-research:wt-researchretry done`. (Developer:
`#11 0.405 Check file:///app/index.ts`.)

The driver. Mounted INTO the image so it imports the image's own `lib.ts`
(the code under test) and calls `delegateCuratorWithRetry` with the exact
init and options `index.ts:373-383` passes, parsing the env with the same
`lib.ts` functions `index.ts:115-116` uses. No LLM, no DB, no research run.
Save as `<S>\drive-curator.ts`:

```ts
import {
  classifyCuratorOutcome, curatorRefusedWorstCaseMs, curatorRetriesFromEnv, curatorTimeoutMsFromEnv,
  curatorTimeoutWorstCaseMs, delegateCuratorWithRetry,
} from "./lib.ts";
const env = (k: string, d = "") => Deno.env.get(k) ?? d;
const CURATOR_URL = env("CURATOR_URL", "http://openbrain-curator:8000").replace(/\/+$/, "");
const CURATOR_TIMEOUT_MS = curatorTimeoutMsFromEnv(Deno.env.get("CURATOR_TIMEOUT_MS"));
const CURATOR_RETRIES = curatorRetriesFromEnv(Deno.env.get("CURATOR_RETRIES"));
const pkg = JSON.parse(env("PKG", "{}"));
const refusedBound = curatorRefusedWorstCaseMs(CURATOR_RETRIES, 1000); // 1 s per connect-fail is generous; measured ~5 ms
const timeoutBound = curatorTimeoutWorstCaseMs(CURATOR_TIMEOUT_MS);
console.log(`drive: url=${CURATOR_URL}/ingest/research-package retries=${CURATOR_RETRIES} timeoutMs=${CURATOR_TIMEOUT_MS} refused_bound_ms=${refusedBound} timeout_bound_ms=${timeoutBound}`);
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
  const bound = (e as Error).name === "CuratorTimeoutError" ? timeoutBound : refusedBound;
  console.log(`ERROR after ${ms} ms (name=${(e as Error).name}, bound_ms=${bound}, within_bound=${ms <= bound + 1000}): ${message}`);
  console.log(`research_jobs.error would be: ${classifyCuratorOutcome({ error: message }).error}`);
  Deno.exit(2);
}
```

Containers, default bridge only. `rr-curator` is the real curator image from
item curatorimg with a dead `DB_HOST` (process up and answering, `/health`
503); `rr-stub` answers 200 JSON; `rr-stall` sends 200 headers plus a partial
body and never finishes; `rr-close` reads the whole request, logs it, and
closes without answering:

    docker run -d --name rr-curator -e DB_HOST=127.0.0.1 -e DB_PASSWORD=x -e MCP_ACCESS_KEY=rr-test-key openbrain-curator:wt-curatorimg
    docker run -d --name rr-stub denoland/deno:2.3.3 eval "Deno.serve({ port: 8000 }, () => Response.json({ thread_id: 'stub-thread', sources_written: 0, stub: true }))"
    docker run -d --name rr-stall denoland/deno:2.3.3 eval 'Deno.serve({ port: 8000 }, () => { const body = new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode("{\"thread_id\":")); } }); return new Response(body, { status: 200, headers: { "content-type": "application/json" } }); })'
    docker run -d --name rr-close denoland/deno:2.3.3 eval 'let n = 0; const l = Deno.listen({ port: 8000 }); console.log("close-after-receipt listening"); for await (const conn of l) { (async () => { const buf = new Uint8Array(65536); let total = 0; let text = ""; while (true) { const r = await conn.read(buf.subarray(total)); if (r === null) break; total += r; text = new TextDecoder().decode(buf.subarray(0, total)); const m = text.match(/content-length: (\d+)/i); const hdrEnd = text.indexOf("\r\n\r\n"); if (m && hdrEnd >= 0 && total >= hdrEnd + 4 + parseInt(m[1], 10)) break; } n++; console.log(`received package #${n} bytes=${total}; closing without answering`); conn.close(); })(); }'
    docker inspect -f '{{.NetworkSettings.Networks.bridge.IPAddress}} {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' rr-curator rr-stub rr-stall rr-close

Expect each to print an IP and exactly `bridge`. Substitute `<CUR>`,
`<STUB>`, `<STALL>`, `<CLOSE>` (developer: 172.17.0.2/.3/.4/.5). Run helper
(PowerShell; in Git Bash prefix `MSYS_NO_PATHCONV=1` so the mount paths
survive; a PowerShell command containing both `docker ... rm` and a
`D:\Open` path is blocked by the agent tool guard - use `docker kill` +
`docker container rm` separately, or Git Bash):

    $img = "openbrain-research:wt-researchretry"
    $D = "-v <S>\drive-curator.ts:/app/drive-curator.ts:ro"
    $C = "-e CURATOR_TIMEOUT_MS=5000 -e CURATOR_RETRIES=3 -e MCP_ACCESS_KEY=rr-test-key"
    $run = "run --allow-net --allow-env --allow-read=/app drive-curator.ts"
    # every case: measure the host wall clock around the docker run as well

**(a) curator STOPPED - error within the refused bound, N attempts logged.**

    docker stop -t 2 rr-curator
    docker run --rm $D -e CURATOR_URL=http://<CUR>:8000 $C $img $run
    docker start rr-curator

Expect exit `2`; `[~4 ms] curator attempt 1/3 failed: TypeError: error
sending request ... client error (Connect): ... Connection refused (os error
111) ...; retrying in 2000 ms`, `[~2006 ms] ... attempt 2/3 ... retrying in
4000 ms`; `ERROR after ~6007 ms (name=Error, bound_ms=9000,
within_bound=true): curator unreachable after 3 attempt(s) in ~6007 ms
(connect-phase failures only, timeout 5000 ms per attempt): TypeError: ...
Connection refused`. On Docker Desktop's bridge a just-stopped container's
IP answers with a RESET (refused), not `EHOSTUNREACH`; the tester's attempt-1
evidence shows an UNCACHED unreachable IP gives `No route to host (os error
113)` after ~3 s - also `(Connect)`, also retried. (Developer: 6007 ms, host
wall 7768 ms.) Also run against a RUNNING curator on a port nothing listens
on (`<CUR>:8001`) - same shape (developer: 6012 ms).

**(b) curator PAUSED longer than the timeout - ONE attempt, no retry lines.**

    docker pause rr-curator
    docker run --rm $D -e CURATOR_URL=http://<CUR>:8000 $C $img $run
    docker unpause rr-curator

Expect exit `2`; **NO `attempt` lines**; `ERROR after ~5003 ms
(name=CuratorTimeoutError, bound_ms=5000, within_bound=true): curator timed
out after ~5003 ms (CURATOR_TIMEOUT_MS=5000, attempt 1/3, not retried - the
curator may still be working on the package)`. Any `retrying in` line is a
FAIL. (Developer: 5003 ms, host wall 6331 ms.)

**(c) the curator answers - first try, no retry.** A 2xx from the REAL
curator needs an embedder, a Postgres with the OB schema and a persist
delegate (`research-curator/index.ts:543-551`), which an isolated bridge
container cannot have; so: (c1) `rr-stub` answers 200 -> exit `0`, no
`attempt` lines, `OK after <~10> ms: {"thread_id":"stub-thread",...}`
(developer: 12 ms); (c2) `rr-curator` with an empty package -> exit `2`, no
`attempt` lines, `ERROR after <~10> ms (name=CuratorHttpError, ...): curator
400: {"error":"claim required"}`, and with `MCP_ACCESS_KEY=wrong` -> `curator
401: {"error":"unauthorized"}` (developer: 11 ms). An `attempt 1/3` line
here is a FAIL: an HTTP answer was retried.

**(d) BODY STALL - 200 headers, body never completes (refutation 2b).**

    docker run --rm $D -e CURATOR_URL=http://<STALL>:8000 $C $img $run

Expect exit `2` (NOT `0`); NO `attempt` lines; `ERROR after ~5001 ms
(name=CuratorTimeoutError, bound_ms=5000, within_bound=true): curator timed
out after ~5000 ms while reading its answer (CURATOR_TIMEOUT_MS=5000, attempt
1/3, not retried - the curator may still be working on the package)`;
`research_jobs.error would be: curator: the research completed but was NOT
filed into Open Brain - curator timed out ... while reading its answer ...`.
`OK after ~5000 ms: {}` here is attempt 1's defect and a FAIL. (Developer:
5001 ms, host wall 6517 ms.)

**(e) CLOSE AFTER RECEIPT - the listener reads the package, then closes.**

    docker run --rm $D -e CURATOR_URL=http://<CLOSE>:8000 $C $img $run
    docker logs rr-close 2>&1 | Select-String "received package"

Expect exit `2`; NO `attempt` lines; `ERROR after <~10> ms
(name=CuratorAfterSendError, bound_ms=9000, within_bound=true): curator
connection failed after the request was sent (attempt 1/3, not retried - the
package may have been received): TypeError: error sending request ... client
error (SendRequest): connection closed before message completed`; and the
listener's log shows **exactly ONE** `received package #1 bytes=227; closing
without answering` line - the package was delivered once, not three times
(attempt 1's `rr-reset2` logged it three times). A second `received package`
line is a FAIL: a resend after receipt. (Developer: 9 ms, one log line.)

**(env) `CURATOR_TIMEOUT_MS=-1` falls back to the default.**

    docker run --rm $D -e CURATOR_URL=http://<STUB>:8000 -e CURATOR_TIMEOUT_MS=-1 -e CURATOR_RETRIES=3 -e MCP_ACCESS_KEY=rr-test-key $img $run

Expect the `drive:` line to print `timeoutMs=180000 ... timeout_bound_ms=180000`
and `OK after <~5> ms` - not attempt 1's `Failed to execute
'AbortSignal.timeout': Argument 1 is outside the accepted range`. (Developer:
`timeoutMs=180000`, `OK after 5 ms`.)

**(f) live mutations** - mount a mutated `lib.ts` OVER the image's
(`-v <S>\lib.mut.ts:/app/lib.ts:ro`), produced from the committed `lib.ts`
with the T1 expressions:

- **C, timeouts made retryable, curator PAUSED** - the WRONG shape:
  `[~5002 ms] curator attempt 1/3 failed: TimeoutError: Signal timed out.;
  retrying in 2000 ms`, `[~12005 ms] attempt 2/3 ... retrying in 4000 ms`,
  `ERROR after ~21010 ms (name=Error, bound_ms=9000, within_bound=false):
  curator unreachable after 3 attempt(s) ...: TimeoutError: Signal timed
  out.` - three resends of one package. (Developer: 21010 ms.) Then the
  unmutated image, still paused: one attempt at ~5003 ms, as in (b).
- **D, body-read errors swallowed, against `rr-stall`** - the WRONG shape:
  exit `0`, `OK after ~5003 ms: {}` (attempt 1's defect, reproduced on
  demand). (Developer: `OK after 5003 ms: {}`.) Then the unmutated image:
  (d)'s `CuratorTimeoutError ... while reading its answer`.
- **B, signal removed, curator PAUSED** - start with `docker run -d --name
  rr-mut ...`, `Start-Sleep 20`, `docker inspect -f '{{.State.Status}}'
  rr-mut` prints `running` (hung past the 5 000 ms bound), `docker logs
  rr-mut` shows only the `drive:` line; then `docker kill rr-mut; docker
  container rm rr-mut; docker unpause rr-curator`. (Developer: still
  `running` at 20 943 ms.)
- **A, retry removed, refused port** (`<CUR>:8001`): exit `2`, NO `attempt`
  lines, `ERROR after <~5> ms (name=TypeError, ...): error sending request
  ... (Connect) ... Connection refused` - the raw error, no `after N
  attempt(s)`. (Developer: 4 ms.) Then the unmutated image: three attempts.

Cleanup: `docker stop rr-curator rr-stub rr-stall rr-close; docker container
rm rr-curator rr-stub rr-stall rr-close`. Keep the `:wt-researchretry` tag.

## T4 - the string that reaches research_jobs.error

Every failing run above printed a final line `research_jobs.error would be:
...`, computed the way `harness.ts:686` + `classifyCuratorOutcome`
(`lib.ts:332`) do it. Check that each begins with `curator: the research
completed but was NOT filed into Open Brain - ` (the curator2 prefix,
unchanged) and continues, per case:

- (a) `curator unreachable after 3 attempt(s) in <n> ms (connect-phase
  failures only, timeout 5000 ms per attempt): TypeError: ... Connection
  refused` - attempt count, elapsed time, last cause;
- (b) `curator timed out after <n> ms (CURATOR_TIMEOUT_MS=5000, attempt 1/3,
  not retried - the curator may still be working on the package)`;
- (d) the same with ` while reading its answer` after the elapsed ms;
- (e) `curator connection failed after the request was sent (attempt 1/3,
  not retried - the package may have been received): TypeError: ...
  (SendRequest): connection closed before message completed`;
- (c2) `curator 400: {"error":"claim required"}` - a verdict keeps its old
  shape.

`research_jobs.error` is `TEXT` (tester, `init-research-jobs.sql:41`): no
column limit applies. Nothing in `classifyCuratorOutcome` moved (T5).

## T5 - harness.ts:686 and classifyCuratorOutcome are unchanged

    git -C OB1 diff --stat d89c126 20ed84b -- integrations/research-service/harness.ts
    git -C OB1 show 20ed84b:integrations/research-service/harness.ts | Select-Object -Index 685
    git -C OB1 diff d89c126 20ed84b -- integrations/research-service/lib.ts | Select-String -Pattern '^-[^-]' | Measure-Object
    git -C OB1 diff --stat d89c126 20ed84b

Expect: the harness diff is EMPTY; line 686 reads exactly `    try { curator
= await deps.delegateToCurator(pkg); } catch (e) { curator = { error:
String((e as Error).message) }; }`; the lib.ts diff has `Count 0` removed
lines (append-only, so `classifyCuratorOutcome` at `:332-359` is
byte-identical); the stat names exactly `index.ts`, `lib.test.ts`, `lib.ts`
under `integrations/research-service/` and nothing else (`3 files changed,
577 insertions(+), 5 deletions(-)`; the 5 deletions are the old
`delegateToCurator` body in index.ts).

## T6 - the gitlink is an integration with the line's pin, not just an ancestor

    $linePin = (git ls-tree refactor/ai-stack-cleanup OB1).Split()[2]
    $pin     = (git ls-tree HEAD OB1).Split()[2]
    git -C OB1 fetch origin
    git -C OB1 merge-tree --write-tree $linePin $pin
    git -C OB1 rev-parse "$pin^{tree}"

Expect the two hashes IDENTICAL. Today `$linePin` is `d89c126` and `$pin` is
`20ed84b`, so the merge-tree is trivially `20ed84b^{tree}` (developer's run:
both printed `7f9f871ce23f851e181083edde20ae4f34e0b170`). If the line's pin
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
