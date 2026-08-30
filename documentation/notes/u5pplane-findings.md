# U5 — personal-plane exclusion, verified end to end (findings)

Branch `work/u5pplane`, 2026-08-30. Item: dark-factory-unification PLAN §2, U5, the
personal-plane half of its *Validated by* column ("an agent instructed to bypass hooks /
reach personal-plane data is mechanically stopped and the attempt is visible in an audit
record"). The hook-bypass half already exists as `scripts/checks/check-hook-attestation.ps1`
and was not touched.

Everything below was produced by a command that was run, not by reading. The commands are
named next to each claim.

**FIX ROUND, 2026-08-30.** This branch was REFUTED 2/2 by adversarial verifiers and did not
merge. The line (`refactor/ai-stack-cleanup`, through merge `e163051`) has been merged in,
and everything below has been re-measured against it — evidence taken against the old base
is not evidence. What the verifiers found, and what happened to it, is F7–F11. One of their
sub-claims is itself refuted, with the command, in F10.

---

## What already existed, and what it did NOT cover

`scripts/checks/smoke-agent-memory.ps1` (run: `.\scripts\checks\smoke-agent-memory.ps1`,
exit 0, 22 checks) already proves, against a real server and a throwaway database:

- a tainted write claiming `exposure:'ops'` is stamped `personal` anyway;
- PII content is stored and demoted, never rejected;
- the exposure label is mirrored onto the linked `thoughts` row;
- personal-plane memories are absent from a **default** recall.

It does **not** cover any of:

1. a caller that explicitly names the personal plane in a recall (`exposure: ["personal"]`);
2. the ops door at all — the smoke script speaks only to the server's REST twin;
3. the thoughts lane as an alternative route to the same content;
4. **any audit record of a refused or filtered access.** This was the real gap and it is F1.

---

## F1 — a refused personal-plane access left NO audit record (CLOSED)

Verified before the change by reading the read path
(`OB1/integrations/kubernetes-deployment/agent-memory.ts`, `performRecall`): the caller's
`exposure` was overwritten from `deps.doorExposure` and then **discarded**. The recall wrote
an `agent_memory_recall_traces` row whose `request_payload` was `{limit, include_unconfirmed}`
— no exposure on either side of the decision. So an agent that asked to read the personal
plane and an agent that asked for nothing produced byte-identical records, and the question
"did anything reach for the personal plane?" had no answer anywhere in the database.

Worse, `agent_memory_audit_events.event_type` has listed `'recall_requested'` since the table
was created (`OB1/docker/init-agent-memory.sql:227`) and **nothing on this server had ever
written one** (`grep -rn "recall_requested" OB1/` returns the schema, the sibling
`integrations/agent-memory-api/index.ts:408` — which is not deployed, it is absent from
`OB1/docker/docker-compose.yml` — and tests). The read path left operational traces and no
governance events at all.

**Closed by** `performRecall` now recording both sides of the decision in the trace payload
and writing a `recall_requested` audit row carrying `requested_exposure`,
`enforced_exposure`, `door_exposure` and `exposure_override_denied`. The decision itself is
a pure function, `decideRecallExposure`, in `agent-memory-policy.ts`.

Proven RED then GREEN at unit level:

- `deno test --allow-all` in `OB1/integrations/kubernetes-deployment` → 115 passed (was 104).
- With `exposure: exposureDecision.enforced` reverted to honour the caller → 1 failed
  ("the widening attempt is refused in the SQL, not only recorded").
- With the audit INSERT deleted → 3 failed (the flag test, the trace-link test, and the
  event-exists test).
- Both neuterings reverted → 115 passed again.

## F2 — a tool denied at the ops door left NO record either (CLOSED)

`openbrain-gateway/app.py` returned a JSON-RPC `-32601` for a tool outside the allow-list and
did nothing else: no line, no row, nothing. An agent probing the door for `search_thoughts`
was invisible.

**Closed by** an audit emitter in `app.py`: one JSON line per governance-relevant decision
(`tool_denied`, `read_filter_override`, `exposure_override_attempt`), on stdout, which compose
retains (`json-file`, 10m × 3). It is deliberately **not** the durable table — a denied call
never reaches openbrain-mcp, so there is no connection to write a row on. Values are
truncated to 64 chars and allowlisted to tool names, field names and plane labels; no content
and no argument blob is ever copied into it.

Byte-for-byte cloud-door behaviour is unchanged: `python openbrain-gateway/smoke_test.py
--defaults` → "cloud defaults preserved byte-for-byte".

## F3 — the drill, and its RED proofs

`scripts/checks/drill-personal-plane-exclusion.ps1`. Run:
`.\scripts\checks\drill-personal-plane-exclusion.ps1` → **exit 0, 62 checks, no FAILs**
(re-measured on the merged line). The earlier note said "26 checks"; a verifier flagged it,
not because it was inflated - it was an UNDERCOUNT - but because the figure was being offered
as evidence and had been transcribed by hand. So the drill now **counts its own PASSes** and
prints the total in its final line. My first attempt at this correction wrote "55", also by
hand, also wrong; the run says 62. A number a human transcribes is a number that drifts from
what ran.

It plants a **synthetic** personal-plane record (`tainted=true`), an ops-plane control and a
cloud-labelled control thought on a throwaway database built from the real initdb chain, and
attacks SEVEN ways across three named lanes. Which lanes, and who actually calls them, is in
`agent-memory-plane/PLAN.md` — the drill no longer claims to cover "the positions an agent
occupies", it names what it hits:

| Attack | Lane | Stopped by | Recorded in |
|---|---|---|---|
| 1 | internal REST (`/agent-memory/recall`, raw body) | `performRecall` forcing the door's plane | durable row, `exposure_override_denied=true` |
| 2 | ops door, MCP `agent_memory_recall` | the tool's zod schema (no `exposure` field) + the same forcing | gateway `exposure_override_attempt` line |
| 3 | ops door, MCP `agent_memory_inspect` **by id** | `performInspect`'s forced plane → `not_found` | durable `access_refused` row naming the tool |
| 4 | ops door, MCP `agent_memory_list_review_queue` (scoped AND unscoped) | `listForReview`'s forced plane | none, by design — a filtered enumeration refuses nothing |
| 5 | ops door, MCP `agent_memory_recall_trace` | the item is DROPPED, not blanked (F7) | durable `access_refused` row naming the tool |
| 6 | ops door, MCP `search_thoughts` | the allow-list (`-32601`) | gateway `tool_denied` line |
| 7 | **cloud door** (`.mcp.json`'s door), `agent_memory_*` + `search_thoughts` | allow-list for the memory tools; the forced `share=cloud` filter for the thoughts lane | gateway `tool_denied` lines |

**Coverage is now asserted, not assumed.** The drill parses `GATEWAY_READ_TOOLS` out of
compose and FAILS if any tool on it was never attacked. Deriving the list and firing at one
of it was worse than hardcoding, because the unattacked names were printed inside a PASS
line — see F10.

Every green is paired with a proof it could have failed:

- the ops **control** must come back on the same query, or "stopped" would just mean "nothing
  matched" (and in the first run it caught exactly that — see F6);
- an ordinary recall must produce an **unflagged** audit row, so the flag is shown to
  discriminate rather than being a constant;
- **RED A**: a scratch copy of the server with one asserted line changed (the door no longer
  overrides the caller) is built as `openbrain-mcp-server:drill-red` and run against the same
  database — the personal fixture **is** returned. The repo tree is never weakened; the patch
  lives in `%TEMP%\pp-drill-red-src` and the drill refuses to build a "red" image if its
  anchor does not match exactly once.
- **RED B**: the ops door's allow-list is widened by env to include `search_thoughts`, and
  then its forced read filter is pointed at the personal plane — the fixture **is** readable.

The ops door's policy is **derived from `OB1/docker/docker-compose.yml`**, not restated in the
drill, so widening the real allow-list cannot leave the drill passing.

## F4 — a documented guarantee that was wrong (CORRECTED)

`documentation/implementation-guide/agent-memory-plane/PLAN.md` said the exposure label is
"mirrored onto the linked thought **so `search_thoughts` enforces the same boundary**".
`search_thoughts` enforces nothing of the kind: its SQL is similarity + an optional
caller-supplied `metadata_filter` and it has no exposure logic
(`OB1/integrations/kubernetes-deployment/index.ts:497`).

I initially took that to mean the mirror was decorative and wrote the drill's RED B expecting
that allowing `search_thoughts` at the ops door would leak the fixture. **It did not** — the
run returned only the ops control. The reason is the door's *second* guard: `_force_read_filter`
injects `metadata_filter={exposure:'ops'}`, and `search_thoughts` **does** honour
`metadata_filter` (`metadata @> $4::jsonb`), matching the mirrored label. So the mirror is the
label and the door's forced filter is the enforcement — genuine defence in depth, and better
than my hypothesis, not worse.

Both the doc line and the drill now say this precisely, and the drill asserts the two guards
separately (allow-list off → still held; allow-list off **and** filter flipped → leaked).

## F5 — the forced read filter is inert for `agent_memory_recall`, load-bearing for `search_thoughts`

`OB1/docker/docker-compose.yml` sets `GATEWAY_READ_FILTER_FIELD: exposure` on the ops door and
calls it "belt-and-braces". That is exactly right for `agent_memory_recall` and understated for
`search_thoughts`:

- `grep -n "metadata_filter" OB1/integrations/kubernetes-deployment/agent-memory*.ts` → **no
  matches**. `RECALL_SCHEMA` has no `metadata_filter` and no `exposure` field
  (`agent-memory-tools.ts:69`), so the injected filter cannot reach any SQL on that tool and
  the recall's boundary is entirely `performRecall`'s server-side forcing.
- for `search_thoughts` the same injected filter *is* the boundary (F4).

Consequence worth keeping in mind: on the MCP lane a caller's `exposure` argument is stripped
by the tool's own schema validation before `performRecall` sees it, so the **durable** row for
that call records `requested_exposure: null` and the **gateway** line is what makes the attempt
visible. The drill asserts this division explicitly (the flagged-durable-row count stays at 1
after the MCP probe) rather than leaving it implied.

## F6 — a drill that passed while every request was rejected (fixed in the drill)

First run: ATTACK 2 reported "STOPPED — the personal fixture is not in the ops door's response"
while the response body was `{"error":"unauthorized"}`. Cause: `Get-OpsGatewayEnv` scraped
`GATEWAY_*` keys out of compose and swept up `GATEWAY_KEY: ${OPS_GATEWAY_KEY:?...}`, handing the
container the literal unexpanded placeholder. An absent fixture is only evidence if the call
succeeded. The drill now excludes `GATEWAY_KEY` (a secret reference, not policy) and checks the
**control first**, failing loudly if the call did not run.

---

---

## F7 — the exposure predicate was in the join, and the join was not the boundary (FOUND + CLOSED)

**Found by the extended drill, in code that had already been fixed once.** Merge `e163051`
bound the exposure plane to every read tool after a verifier proved `agent_memory_inspect`
and `agent_memory_list_review_queue` were leaking. `performRecallTrace` got its predicate in
the same pass — inside a `LEFT JOIN`:

```
LEFT JOIN agent_memories am ON am.id = ri.memory_id AND <exposure clause>
```

A `LEFT JOIN` can only null the columns it takes from the **joined** side. `summary` and
`review_status` came back null, correctly. `memory_id`, `rank`, `similarity` and
`use_policy_snapshot` came back **intact**, because they are selected from
`agent_memory_recall_items`, which the predicate never touched. And no audit row.

So a recall trace remained a working ENUMERATOR of the personal plane, and what it enumerated
was **ids** — precisely the input `agent_memory_inspect` takes. The tool that had just been
closed against the plane was being fed by the tool beside it.

RED, then GREEN, twice over:

- drill ATTACK 5 on the merged line: `FAIL EXPOSURE LEAK: the recall trace discloses the
  off-plane memory's id` and `FAIL expected exactly 1 access_refused row for recall_trace,
  got '0'`. After the fix: both PASS.
- 4 new deno tests. Proved red by `git checkout HEAD -- agent-memory-tools.ts` and re-running:
  **3 of 4 fail** (`21 passed | 3 failed`). The 4th — "a trace with nothing off-plane writes
  NO audit row" — passes both ways **by design**: it is the discrimination control that stops
  the audit assertion from being satisfiable by a writer that always writes.
- full OB1 suite after: `deno test --allow-net --allow-env .` → **126 passed, 0 failed**.

The fix keeps the `LEFT JOIN` (an existing test pins the join kind, and an `INNER JOIN` would
fold the plane predicate into the row set where nothing can observe it), adds
`(am.id IS NOT NULL) AS on_plane`, drops the failed rows whole, and writes an
`access_refused` row per withheld item. `on_plane` never reaches the caller. The withheld
COUNT is deliberately not returned — "3 items you may not see" confirms their existence,
which is the disclosure `not_found` exists to avoid.

**The lesson, which is the reusable part:** *the predicate reached the SQL* and *the row left
the result* are different claims. The pre-existing test `recall_trace's items are bounded by
the plane too` asserted the first and read as if it proved the second. That is the same shape
as the over-claim this item keeps re-learning — proving a boundary at one point and
describing the surface as contained.

---

## F8 — the drill gave a FALSE RED under concurrency, and it burned two verifier sessions (CLOSED)

Both verifiers hit it. The drill minted a unique `$MARKER` *"so a stale row can never be
mistaken for this one"* and then used it in **none** of the three counting queries — they
scoped to a constant `workspace_id='ws-drill'`. Container names, the network, image tags and
the initdb temp dir were hardcoded constants; only ports were parameters. So a second run
force-removed the first run's containers at startup and shared its database, and both agents
got a RED on correct code, with messages that misdiagnosed the cause.

Fixed at the root rather than at the queries: **`$RunId`** (random per run, overridable)
suffixes every container, the network, both image tags and all three temp paths; ports are
allocated free by default; `Remove-DrillStack` touches this run's names only; and the
fixtures are planted under `workspace_id = "ws-drill-$MARKER"`, so every count in the file is
marker-scoped by construction rather than by remembering to add a clause.

**No plane lease is taken, deliberately.** CLAUDE.md requires `lease.ps1 -Acquire` for a test
that mutates a plane or needs one stable. This drill needs no shared plane — a lease would
only serialise what isolation lets run in parallel, and a gate two agents cannot both execute
is not a usable dark-factory gate.

PROVED, not argued: two full drills launched simultaneously as PowerShell jobs
(`Start-Job` x2, `Wait-Job`) → run `7b63502b` and run `27ac05df`, **both `EXITCODE=0`**, both
printing `PERSONAL-PLANE EXCLUSION DRILL PASSED`.

---

## F9 — the drill did not check its own `docker run` exit code (CLOSED)

Observed by a verifier, not theorised: `docker run` failed twice with `Conflict. The container
name "/pp-drill-mcp" is already in use`, and the drill printed `PASS built ...`, `PASS
openbrain-mcp ... is answering`, `PASS ops door is answering` immediately after. `Wait-Http`
happily passed off whatever was already listening on the port. Only the two `docker build`
calls checked `$LASTEXITCODE`.

Every creating docker call now goes through `Invoke-DockerOrThrow`, which fails and aborts on
a non-zero exit. And the database is asserted **empty** before anything is planted
(`memories/audit/thoughts/traces = 0/0/0/0`) — every assertion below it is a count or an
absence, and both are meaningless on a database whose starting state was never established.

PROVED by reproducing the verifier's exact scenario: squat the name
(`docker run -d --name pp-drill-sq1-mcp alpine sleep 900`), then run with `-RunId sq1`:

```
FAIL  start openbrain-mcp pp-drill-sq1-mcp on :60838 - docker exited 125
      docker: Error response from daemon: Conflict. The container name "/pp-drill-sq1-mcp"
      is already in use by container "8da1fa427e47..."
aborted: docker failed: start openbrain-mcp pp-drill-sq1-mcp on :60838
2 DRILL CHECK(S) FAILED          EXIT=1
```

Where it previously printed three PASS lines and carried on.

**Not fixed, stated:** `Start-ObInitdb` in `scripts/checks/lib/ob-initdb.ps1` still does not
check `$LASTEXITCODE` on its own `docker run`. It fails CLOSED anyway — it polls for
`PostgreSQL init process complete` and returns `$false` on timeout, which the drill treats as
fatal — so the hole is latent, not live. It is shared by other checks, and changing it in a
fix round would put their behaviour at risk for no evidence gain here.

---

## F10 — the coverage claim was vacuous, and the door with real traffic had none (CLOSED)

The sharpest of the two verdicts. The drill **printed the hole in a PASS line**:

```
PASS ops-door policy DERIVED from compose (read tools: agent_memory_recall,
     agent_memory_inspect, agent_memory_recall_trace, agent_memory_list_review_queue)
```

It derived four read tools and attacked one. Two of the three it skipped had no server-side
exposure filter at all. The stated safeguard — "widening the real allow-list can't leave it
passing" — was itself vacuous, because the list was derived and never iterated.

Closed two ways. Every tool on the derived list now has its own named ATTACK section, and a
**coverage gate** fails the drill naming any tool it parsed but never fired at — so the next
tool added to the door cannot ride in unexamined.

And the scope claim is corrected. The drill said it attacked "the three positions an agent
actually occupies". It did not: `.mcp.json` points every Claude Code / cloud agent at
`127.0.0.1:8061`, the **cloud door**, which had zero coverage. ATTACK 7 adds it — a gateway
started with env DERIVED from compose's `openbrain-gateway` service (default profile), where
the memory tools are denied by allow-list and `search_thoughts` is bounded by the forced
`share=cloud` filter. That last one previously rested on a **code comment**
(`agent-memory.ts`: *"No `share:'cloud'` label ... the cloud gateway's forced share=cloud read
filter therefore excludes these automatically"*) with no test anywhere. It now has a red
phase: label the mirrored thought `share=cloud`, change nothing else, and the cloud door hands
the fixture over — `PASS RED CONFIRMED (ATTACK 7b)`. The label is doing the work, as claimed.

**One verifier sub-claim is FALSE, and here is the command.** Verdict 4 says "a repo-wide grep
for 8062 ... returns exactly one hit: a row in `workspace-stacks.md`. No client anywhere is
configured to use the ops door." Two clients are:

- `scripts/claude-sessions-bridge/memory_writer.py:26` — `OPS_DOOR = os.environ.get(
  "CLAUDE_MEMORY_OPS_URL", "http://127.0.0.1:8062")`, imported at `bridge.py:1770`. It was on
  this branch when the branch was refuted: `git cat-file -e
  70230c9:scripts/claude-sessions-bridge/memory_writer.py` succeeds.
- `scripts/agent-harness/durable_checks.py:179` — same door; landed later, with U3.

Both are **WRITE** callers, and `memory_writer`'s two feature flags default off. So the
verifier's *substance* holds and is what drove the work — the door whose READ tools were
leaking has no reader, while the door every agent demonstrably holds open had no coverage —
but "no client anywhere" is not true, and the corrected statement is now in
`agent-memory-plane/PLAN.md`.

Worth recording *why* both of us got this wrong in opposite directions: **`.mcp.json` is
gitignored.** It is absent from every worktree, so a grep run inside one finds nothing and
concludes nothing. My own first pass here also ran `grep -rn --no-ignore 8062 . 2>/dev/null`,
which is a **ripgrep** flag — GNU grep rejected it, the error went to `/dev/null`, and the
empty output looked like a clean negative result. A search that failed to run is not a search
that found nothing; the `2>/dev/null` is what made the difference invisible.

---

## F11 — what this round did NOT close, stated mechanically

- **The drill is wired into no gate.** `grep -rn 'drill-personal-plane' .githooks/ scripts/`
  returns only the script itself. `.githooks/pre-commit` runs five fast checks (secrets, line
  endings, gateway routing, project configs, env scope); this drill builds two docker images
  and takes about four minutes, so putting it there would make every commit in the repo pay
  for it. `queue.ps1:436` gates `-Submit` on `check-hook-attestation.ps1`, which is a
  seconds-long git read, not a comparable slot. PARKED with the reason, not papered over. Its
  correct home is a U6 gate profile — a check that fires when
  `OB1/integrations/kubernetes-deployment/agent-memory*.ts`, `openbrain-gateway/app.py` or the
  compose gateway env is touched — which is new attestation machinery and not this fix round's
  scope.
- **Production runs an image built before any of this.** `docker ps` → `openbrain-gateway` and
  `openbrain-ops-gateway` on `openbrain-gateway:local`, `Up 5 hours`. The drill proves **the
  source tree's** boundary; whether the deployed containers run that tree is the deploy gate's
  question. Deploying from an unmerged branch would put unreviewed code in the memory plane, so
  this waits for the merge. The drill's header now says so explicitly, rather than letting "the
  attempt is visible in an audit record" read as a statement about production.
- **`performRecallTrace` does not scope the TRACE row itself** — only its items. Any trace id
  reads back its `workspace_id`, `query` and `request_payload` regardless of workspace. The
  `query` is caller-supplied text, so this is a real if narrow disclosure surface. Not fixed:
  the trace table carries no exposure column, so deriving a trace's plane is a design question
  rather than a predicate, and inventing one inside a fix round is exactly how the F7 class of
  error gets made. Recorded for whoever does §1.2's next pass.
- **U5's column is still two halves and this branch is one.** The hook-bypass limb is
  `check-hook-attestation.ps1` plus the sibling branches `work/u5judge` / `work/u5proxy`. Both
  verifiers said so and both were right. This branch closes the personal-plane limb; it must
  not merge under a banner that says U5 is closed.

---

## Open — verified, deliberately not fixed here

- ~~**The OB1 gitlink points at a commit that is not on the OB1 remote.**~~ **CLOSED this
  round.** Pushing stopped being class 4 on 2026-08-30 (§C.2 narrowed), so the fix was the
  cheap one: push first, verify, then bump. `git -C OB1 push -u origin
  work/u5-personal-plane-audit` → new branch; `git -C OB1 branch -r --contains 679889b` →
  `origin/work/u5-personal-plane-audit`; `git ls-remote origin work/u5-personal-plane-audit`
  → `679889b0dd2e…`. Verified in the OPERATOR'S OWN CHECKOUT too, which is where the
  verifier's `malformed object name` came from: after `git -C "D:/Open WebUI/ai-stack/OB1"
  fetch origin`, `git log --oneline -1 679889b` resolves. The originally-refuted `0c7af57`
  is now reachable as well — it is an ancestor of the pushed tip.
- **`documentation/implementation-guide/agent-memory-plane/PLAN.md`'s gate table is stale.**
  It says 1.2 is "PARTIAL — 2 of 7 tools" (all seven are registered in
  `agent-memory.ts` `registerAgentMemory`) and 1.4 is "NOT MET — built the wrong thing,
  reverted" (`openbrain-ops-gateway` is in `OB1/docker/docker-compose.yml:222` with
  `GATEWAY_PROFILE: ops` and its own key). Not this item's file to rewrite; flagged so the next
  reader does not trust it.
- **The drill now cleans up after itself.** Images are tagged `:drill-<runid>` and removed
  in the `finally` block along with the containers, network and temp dirs (`-KeepUp` keeps
  them and prints the teardown command). Verified after five runs: `docker ps -a --filter
  name=pp-drill` and `docker network ls --filter name=pp-drill` are both empty. The older
  fixed-name tags `openbrain-mcp-server:drill`, `:drill2`, `:drill-red`,
  `openbrain-gateway:drill`, `:drill2` are litter from the PREVIOUS naming scheme (mine and
  the verifiers'); nothing produces or removes them any more, and they are left alone rather
  than deleted by a session that cannot prove whose they are. None is `:local`.
- **Production was never touched, and is verified so.** During the drill runs `docker ps`
  shows `openbrain-gateway` and `openbrain-ops-gateway` still on `openbrain-gateway:local`,
  `Up 5 hours (healthy)`.
- **`promote_exposure` is still absent from the schema's review-action CHECK**, so a memory
  demoted to `personal` cannot be elevated. Pre-existing, already recorded in DECISIONS.md.

---

## DECISIONS entries to append

## 2026-08-30 · U5 · class 2 — the missing half of U5 was the AUDIT, and it is now two records
DECISION: U5's column is "mechanically stopped AND the attempt is visible in an audit
          record". The stopping was already built and already proven. The visibility was
          absent on BOTH lanes, and the two lanes needed different answers:
          (a) a recall that names another plane now writes a durable row -
          `agent_memory_audit_events(event_type='recall_requested')` with
          `requested_exposure` / `enforced_exposure` / `door_exposure` /
          `exposure_override_denied`, plus the same fields on the recall trace;
          (b) a tool DENIED at the ops door never reaches the server, so there is no
          connection to write a row on - it emits a structured audit line on the
          gateway's stdout instead (`tool_denied`, `exposure_override_attempt`,
          `read_filter_override`), which compose retains at 10m x 3.
CITED:    §C.2 class 2 (a discovered gap; the option chosen is the most reversible and
          reuses existing surface). `recall_requested` was ALREADY in the schema's
          event_type CHECK and had no writer, so this needed no migration - the durable
          half is additive code against a table that was waiting for it.
WHY NOT:  a durable row for the gateway denial was rejected: the gateway holds no database
          credential and the denied call never reaches openbrain-mcp. Inventing a
          write-back endpoint for it would be new attack surface to record an attack.
REVERT:   revert the OB1 commit + the gitlink (the audit INSERT and decideRecallExposure
          are additive; no schema change to unwind), and revert the `_audit` block in
          `openbrain-gateway/app.py`. The drill fails afterwards, loudly, which is the
          intended behaviour of removing the thing it checks.
EVIDENCE: `scripts/checks/drill-personal-plane-exclusion.ps1` exit 0 (62 checks,
          `deno test --allow-all` 115 passed; `smoke-agent-memory.ps1` exit 0 (no
          regression); `python openbrain-gateway/smoke_test.py --defaults` byte-for-byte.

## 2026-08-30 · U5 · class 2 — the drill derives the ops-door policy from compose
DECISION: `drill-personal-plane-exclusion.ps1` parses `openbrain-ops-gateway`'s
          `GATEWAY_*` environment out of `OB1/docker/docker-compose.yml` rather than
          carrying its own copy of the allow-list, and excludes `GATEWAY_KEY` (a secret
          REFERENCE, not policy).
CITED:    §C.2 class 2 + the house pattern in `scripts/checks/lib/ob-initdb.ps1`, which
          derives the initdb chain from compose for the same reason: a second copy goes
          stale and the check keeps passing against its own opinion.
REVERT:   replace `Get-OpsGatewayEnv` with a literal hashtable.

## 2026-08-30 · U5 · CORRECTION — "search_thoughts enforces the boundary" was wrong, and the truth is better
DECISION: Corrected `documentation/implementation-guide/agent-memory-plane/PLAN.md`, which
          said the mirrored exposure label means `search_thoughts` "enforces the same
          boundary". It does not: that tool has no exposure logic (index.ts:497). The
          enforcement is the ops door's forced `metadata_filter`, which `search_thoughts`
          DOES honour and which matches the mirrored label. Mirror = label; door = gate.
CITED:    §C.7 (an executable check, not prose, decides) and §0 A9 (verify before relaying).
          Found by writing the drill's RED phase to expect a leak and NOT getting one -
          the prose and my own hypothesis were both wrong in the same direction.
REVERT:   restore the previous sentence; the drill's two separate sub-assertions would
          then contradict the document.

## 2026-08-30 · U5 · class 2 — a recall trace's items are DROPPED off-plane, not blanked
DECISION: `performRecallTrace` selects `(am.id IS NOT NULL) AS on_plane` from its existing
          LEFT JOIN, removes any row that failed the join from the result entirely, and
          writes an `access_refused` audit row naming `agent_memory_recall_trace` for each.
          The join stays LEFT; `on_plane` never reaches the caller; the withheld COUNT is
          not returned.
CLASS:    2 — a discovered gap in code that had already been fixed once, closed the way the
          sibling tool was closed rather than a new design.
CITED:    §C.7 (an executable check decides) and the `performInspect` precedent from merge
          e163051 — the closest existing house pattern, which is §C.2's tie-breaker.
          The predicate was already there; a LEFT JOIN can only null the columns it takes
          from the joined side, so `memory_id`, `rank`, `similarity` and
          `use_policy_snapshot` were returned intact — and a memory_id is what
          `agent_memory_inspect` consumes.
WHY NOT:  an INNER JOIN was rejected: it would fold the plane predicate into the row set
          where nothing can observe it, and an existing test pins the join kind. Returning
          a withheld-count was rejected: "3 items you may not see" confirms their existence,
          which is the disclosure `not_found` exists to avoid.
REVERT:   revert OB1 679889b (additive — no schema change; `access_refused` already exists
          from e163051) and re-bump the gitlink. The drill's ATTACK 5 then fails loudly,
          which is the intended behaviour of removing the thing it checks.
EVIDENCE: 3 of 4 new deno tests fail against the pre-fix file restored from HEAD
          (`21 passed | 3 failed`); `deno test --allow-net --allow-env .` → 126 passed, 0
          failed; drill ATTACK 5 red then green.

## 2026-08-30 · U5 · class 2 — the drill is isolated per run, and takes NO plane lease
DECISION: `drill-personal-plane-exclusion.ps1` derives every container name, the network,
          both image tags, all three temp paths and the fixtures' `workspace_id` from a
          per-run `$RunId` / `$MARKER`; ports are allocated free by default;
          `Remove-DrillStack` touches only this run's names. It acquires no plane lease.
CLASS:    2 — the alternative (a lease) is defensible, so this is a judgment call and is
          logged with the assumption stated.
CITED:    §C.7 ("nothing merges unrefuted" presumes a check other people can run) and
          CLAUDE.md's lease rule, which scopes to tests that MUTATE a plane or need one
          STABLE. This drill builds its own throwaway plane and touches no shared one, so a
          lease would serialise what isolation lets run in parallel — and a gate two
          parallel agents cannot both execute is not a usable dark-factory gate.
ASSUMPTION: that docker can build two differently-tagged images from the same context
          concurrently. Held across the two-job run; if it ever does not, the fallback is a
          lease on a new `drill-images` name rather than re-serialising the whole drill.
REVERT:   pass a fixed `-RunId` and the old ports; the constants come back for one run.
EVIDENCE: two drills started simultaneously as PowerShell jobs — runs `7b63502b` and
          `27ac05df`, both exit 0. Before this change, two concurrent runs mutually
          sabotaged and both verifiers reported a false RED.

## 2026-08-30 · U5 · class 2 — a derived allow-list must be ITERATED, and the drill fails if it is not
DECISION: the drill marks each tool it attacks and, after the attacks, FAILS naming any tool
          in compose's `GATEWAY_READ_TOOLS` that no section fired at. Every creating docker
          call goes through `Invoke-DockerOrThrow`; the database is asserted empty before
          any fixture is planted.
CLASS:    2 — a discovered gap in the item's own safeguard.
CITED:    §0 A6 (prose verification FALSIFIED) applied to the drill itself. Deriving the
          list and attacking one of it was WORSE than hardcoding, because the unattacked
          names were printed inside a PASS line and read as coverage. Two of the three
          skipped tools were leaking.
REVERT:   delete the coverage block; the drill then passes with the same holes it had.
EVIDENCE: coverage gate green at 4/4 with each tool's attack named. Exit-code check proved
          by squatting a container name — `docker exited 125`, drill aborts, EXIT=1, where
          it previously printed three PASS lines and continued.

## 2026-08-30 · U5 · CORRECTION — the drill's scope claim, and a verifier sub-claim, were both wrong
DECISION: `agent-memory-plane/PLAN.md` and the drill header no longer say the drill attacks
          "the three positions an agent actually occupies". They name the three lanes, who
          calls each, and what is NOT attacked (the running containers; the real personal
          plane). A cloud-door lane (ATTACK 7) was added because that is the door `.mcp.json`
          points at and it had zero coverage.
CITED:    §C.7 — the operator audits these notes and not the diffs, so an incorrect boundary
          statement in them is the failure mode C.7 is most exposed to.
ALSO:     the verifier's own sub-claim — "no client anywhere is configured to use the ops
          door" — is FALSE. `scripts/claude-sessions-bridge/memory_writer.py` (imported at
          `bridge.py:1770`) and `scripts/agent-harness/durable_checks.py` both call
          :8062. The first was on this branch when it was refuted:
          `git cat-file -e 70230c9:scripts/claude-sessions-bridge/memory_writer.py`
          succeeds. Both are WRITE callers, so the narrower true statement — nothing READS
          through the ops door — is what is recorded. `.mcp.json` is GITIGNORED and absent
          from every worktree, which is why a grep inside one concludes nothing either way.
REVERT:   restore the previous sentences; the drill's ATTACK 7 would then be uncited.

## 2026-08-30 · U5 · class 2 — the OB1 gitlink is pushed BEFORE it is bumped
DECISION: `git -C OB1 push -u origin work/u5-personal-plane-audit` ran before the parent's
          gitlink was moved to 679889b.
CLASS:    2 — previously blocked as class 4; §C.2's 2026-08-30 narrowing made remote pushes
          autonomous, which turned a merge blocker into a one-line fix.
CITED:    CLAUDE.md, "never bump the gitlink to a commit that isn't on the OB1 remote" — a
          fresh `git clone --recurse-submodules` cannot resolve a pointer that exists only
          in one worktree's module store.
REVERT:   `git -C OB1 push origin --delete work/u5-personal-plane-audit` and revert the
          gitlink commit. Do NOT delete the branch while the parent points at it.
EVIDENCE: `git ls-remote origin work/u5-personal-plane-audit` → `679889b0dd2e…`;
          `git -C OB1 branch -r --contains 679889b` → `origin/work/u5-personal-plane-audit`.
          Verified in the OPERATOR'S checkout, which is where the refutation's
          `malformed object name` came from: after `git fetch origin` there,
          `git log --oneline -1 679889b` resolves. The originally-refuted `0c7af57` is now
          reachable too, as an ancestor of the pushed tip.
