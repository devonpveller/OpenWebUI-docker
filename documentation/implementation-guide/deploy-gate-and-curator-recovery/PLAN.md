# PLAN — deploy is part of done: harness gate 5d, curator recovery, research reliability

> Status: 📝 draft 2026-09-06 · author: Claude session (assessment for the operator)
> Trigger: `openbrain-curator` restart loop since 2026-09-05 23:39 (81+ restarts by 14:00).
> Owner of each phase is a harness item; this document is the shared anchor material.
> Findings that fall out of the work go to `documentation/notes/research-curator-broken-pipe-2026-08-31.md`.

## 0. What happened, in one chain

Every link below was verified by reading the cited line or running the read-only command.

1. **curator2** re-implemented the lost `curatorpool` work and landed OB1 `3ab8a3a` (pool.ts, 2026-09-04). The parent merge is `08c4ae1`. Its own message says: *"Merging deploys neither service — both are Deno images needing a rebuild + recreate."*
2. The test plan case T5 (`curator2.plan.md:149-154`) said *run it against images built from this branch*. The tester's evidence marked T5 `PASS (scoped — read the caveat)` and stated the images were not built. `queue.ps1 -Pass` never opens the plan or the evidence (`queue.ps1:1053-1175`; the only readers of `test_plan` are writes and one `Write-Host` at `:1032`), so a scoped pass and a pass are the same byte to the tool.
3. Gates 5b/5c fire on a gitlink bump but glob only `OB1/recipes` (`check-ob1-recipe-tests.ps1:149-154`, `check-ob1-deno-recipes.ps1:107`). `OB1/integrations/` — 8 Dockerfiles — is outside both.
4. `research-curator/Dockerfile:10-13` copies four named `.ts` files and never `pool.ts`; there is no build-time `deno check`. Its siblings `research-service/Dockerfile` and `kubernetes-deployment/Dockerfile` already use `COPY *.ts` + `rm *.test.ts`, and research-service adds `RUN deno check index.ts` with a comment describing this exact crash loop. Third recurrence of the same defect class; the fix was never ported one directory over.
5. Nobody deployed for 30 hours (the pipeline terminates at `merged`; MERGE-PROTOCOL §6 *"does not cover deploy verification"*). At 23:38 on 09-05, three images were rebuilt together (curator, mcp-server, research — the shape of a `compose up -d --build` for the srcadm research pin). The curator image failed 17 s later. Who ran it is unverified (not in the operator's shell history).
6. Nothing noticed. `stack.ps1 health` only reads `docker ps --filter health=unhealthy` (`stack.ps1:127-129`) and the curator has no `healthcheck:` (compose `:470-513`). `check-openbrain-health.ps1` names the curator zero times. The sysadmin MCP `stack_health` *would* flag "restarting" (`sysadmin.py:339`) but is on-demand only. `emergency-recovery.ps1` never rebuilds an OB1 image and would report `27/28 running` at INFO.
7. Blast radius is latent, not realised: no research job has run since the rebuild (last three 09-05 05:16). The next one will POST to a refused port with no timeout and no retry (`research-service/index.ts:352-361`), be classified `status='error'` by the honesty gate, and the live OWUI Deep Research tool (owui_id `deep_research`, last updated 2026-08-16, no banner string) will render "Research failed" and drop the report — the regression the curator2 anchor forbade, because `owui/tools/deep_research.py` is deploy-by-paste and was never pasted.
8. `curatorpool` (state `ready-to-test`) pins OB1 `22f41b6`, which exists in no repository. `-List`/`-Show` never resolve a SHA (`queue.ps1:531-576`), so the zombie reads as live work and was named as the owner of this fix.

Proof the fix is one line: a scratch copy of the pinned source with `COPY pool.ts ./` added built and `deno check index.ts` passed inside the image (probe removed afterwards).

## 1. Decision the operator makes first

**Paste `owui/tools/deep_research.py` into OWUI now, ahead of everything else.** It is merged, tested (curator2 T6 RED→GREEN), and its deploy is already the operator's gated step. Until it is live, every research run during the days below discards its report on the interactive path. This is independent of the image and costs five minutes. Verify with the same query used today: the live `tool.content` must contain `Not saved to Open Brain`.

Everything else follows the order the operator asked for: harness first, the curator as the harness's first live test, then research reliability, then hardening.

## 2. Phase A — harness: close the seams that let it pass

One harness item per line unless noted. Each has an anchor, a plan, a tester who did not write it, a reviewer who did not write it.

### A1. Gate 5d — image build on gitlink bumps (`check-ob1-integration-images.ps1`)

- **Trigger:** staged `OB1` gitlink, same skip/refuse shape as 5b/5c (`check-ob1-recipe-tests.ps1:90-118` gives staged pin, old pin `HEAD:OB1`, staged==disk clause, `Git-InOB1` wrapper).
- **Scope:** `git -C OB1 diff --name-only <oldPin> <stagedPin> -- integrations/` → every `integrations/<svc>/` that contains a `Dockerfile`.
- **Two halves, static then build:**
  1. *Static (seconds, no Docker):* if the Dockerfile uses per-file `COPY`, every `./x.ts` reachable from `index.ts` must be covered. Message names the file and the import line.
  2. *Build:* `docker build -t ob1-gate/<svc>:<stagedSha7> OB1/integrations/<svc>` then `docker run --rm <img> deno check index.ts`; `rmi` afterwards. REFUSES (does not warn) when Docker is unavailable, mirroring 5c's deno rule.
- **Out-of-hook mode:** `-OldPin/-NewPin` params so a tester can run RED/GREEN without staging.
- **Wiring:** `.githooks/pre-commit` after line 119, identical five-line shape; row in `.githooks/README.md` after line 29.
- **Acceptance:** `-OldPin 48c0363 -NewPin a07103b` exits non-zero naming `research-curator` and `pool.ts` (RED on the real incident). Against the Phase B pin it exits 0. A pin touching only `recipes/` exits 0 in <1 s.

### A2. A pass must see the plan (`queue.ps1 -Pass`)

- At `-Submit`, record `plan_sha256` of the plan file (closes the "no plan hash" gap; `Copy-IntoQueue` `queue.ps1:274-283` is the place).
- At `-Pass`: parse case headings from the plan (`^## (T\d+|Case \d+)`) and require every case to appear in the evidence with a verdict token `PASS` on its heading line. `FAIL`, `SKIPPED`, `scoped`, or absent → Die listing the cases; the tester's options are to run the case, `-Fail`, or `-PlanInadequate` (a case that cannot be executed in the tester's environment is a plan defect, not a scoped pass). Verdicts are recorded per case in `results[]`, not one per item.
- If the plan file's hash changed since submit, refuse: evidence for a different plan.
- **Acceptance:** replaying curator2's real plan and evidence through the new `-Pass` is refused, naming T5 and T6. A synthetic all-PASS evidence passes. `verify-queue-defects.ps1` gains D8 (scoped pass) and D9 (plan hash drift).

### A3. Deploy is a queue state, not a footnote

- `-Merged` derives **deploy surfaces mechanically from the merge range**, never from the author's list: gitlink moved and touches `OB1/integrations/<svc>/` with a Dockerfile → `image:<compose service>`; any `owui/**` file changed → `paste:<file>`; a `:local`-tagged build context outside OB1 changed → `image:<svc>`. Stored as `deploy_pending[]`.
- New state `deployed` after `merged` (terminal moves). New verb `-Deployed -Id -By -Evidence`, operator-gated (MERGE-PROTOCOL §4 already says deploy is operator-approved). Evidence must name, per surface, the image's source SHA and the container's health state, and must address the merge message's "MUST BE AFTER DEPLOY" list.
- `-List` shows `[UNDEPLOYED: image:openbrain-curator, paste:owui/tools/deep_research.py]` on merged items until `-Deployed`.
- Fix the flag that hides signal: `line_mergeable` is set only at `-Submit` (`queue.ps1:919,932`) and never cleared, so 25 merged items read `[needs hand-off]`. Suppress the flag on terminal states.
- **Acceptance:** a `-Merged` on the Phase B item lists exactly the curator image surface; `-Deployed` without health evidence is refused; `-List` no longer flags merged items as hand-offs.

### A4. Zombie items cannot look alive

- `-List` and `-Show` resolve `submitted_sha` / `tested_at_sha` / `merged_sha` (`git cat-file -e <sha>^{commit}`) and, for each, the OB1 gitlink at that SHA in the item's worktree clone. Unresolvable → `[UNRESOLVABLE: OB1 22f41b6]` and the row sorts to the top.
- Close `curatorpool` now: `-CloseOut -Id curatorpool -Reason "superseded by curator2 (08c4ae1); OB1 half destroyed, see that merge message"`. `-CloseOut` accepts any non-terminal state (`queue.ps1:473-476`).
- Correct the owner line in `documentation/notes/sysadmin-disk-alert-silence-2026-09-06.md:86-88` — belongs to the diskalert item's author, not this plan.

### A5. Protocol text

- MERGE-PROTOCOL "Cases that keep earning their place" (`:205-226`) gains: *if the diff touches a directory that ships as an image, the plan builds that image from the branch (`:wt-<id>`) and starts it once, or runs `deno check` inside it. A case the tester cannot execute is a plan inadequacy, never a scoped pass.*
- §6 "does not cover deploy verification" is replaced by the A3 step; §4 keeps deploy human-gated.
- `SERVICE-LIFECYCLE.md` row 11: a service with `build:` is deployed by `build` + `up -d`, never `up -d` alone; recovery must not assume `:local` is current.

## 3. Phase B — the curator as the gate's first live test

One item, working name `curatorimg`. It is the validation run for A1–A3: the gate must go RED on today's pin and GREEN on this item's pin, the pass must be a full pass, and the merge must produce a deploy surface that is then closed with `-Deployed`.

**Artifact (OB1, `integrations/research-curator/`):**
- `Dockerfile` → the sibling pattern verbatim: `COPY *.ts ./`, `RUN rm -f ./*.test.ts`, `RUN deno check index.ts`. Also drop `proof/` from the image (not copied by `*.ts`; confirm nothing imports it).
- `OB1/docker/docker-compose.yml`: curator `healthcheck:` copied from workbench (`:839-844`, same base image, same `/health` on :8000); `openbrain-research.depends_on.openbrain-curator: service_healthy` (`:538-539`).

**Artifact (parent):**
- gitlink bump (push OB1 first; ls-remote; integration merge if the line's pin moved — see `ob1-gitlink-integration-traps`).
- `scripts/stack/stack.ps1` health: `Probe` on `http://127.0.0.1:8816/health` (SERVICE-LIFECYCLE row 5, skipped when the curator was added).
- `scripts/checks/check-openbrain-health.ps1`: add `openbrain-curator` to the container list (and the five other absent workers: chunk-worker, suggestion-worker, grounding-backfiller, workbench, extract — verify each has a state worth probing).
- `scripts/lib/stack-services.json:278-282`: `host_health` for the curator; keep `critical: false` unless the operator wants research blocked on it.

**Test plan (must contain):**
- T0 gitlink reachability (the curator2 T0, unchanged).
- T1 RED: `check-ob1-integration-images.ps1 -OldPin 48c0363 -NewPin a07103b` fails naming pool.ts.
- T2 GREEN: same script `-OldPin a07103b -NewPin <this item's OB1 sha>` passes, and the build log shows `deno check` ran.
- T3 Image starts: build `openbrain-curator:wt-curatorimg`, run with `DB_HOST` pointing at a dead host, `GET /health` answers `{"ok":...,"db":false}` — the process is up even when the DB is not. Never on `ai-stack_*` networks; never `:local`.
- T4 `docker compose -f OB1/docker/docker-compose.yml config` renders the healthcheck and the `service_healthy` condition.
- T5 Mutation: remove the `RUN deno check` line and delete `pool.ts` from a scratch copy; the build must succeed and the container must crash — then restore `deno check`; the build must fail. Proves the check bites.
- T6 Every case has a PASS on its heading line (A2 enforces it; the tester records it anyway).

**Deploy (operator, then `-Deployed`):**
1. `docker compose -f OB1/docker/docker-compose.yml build openbrain-curator` then `up -d openbrain-curator openbrain-research` (research recreates because its depends_on changed).
2. `docker inspect openbrain-curator --format '{{.State.Health.Status}} {{.RestartCount}}'` → `healthy 0`.
3. The curator2 merge message's post-deploy list, now executable: run one real research job → `SELECT status, error, progress FROM research_jobs` shows `curator=filed`; the OWUI tool shows the report; then, in a test window, point one job at a failure and confirm `status='error'`, `result` populated, banner shown on both chat paths.
4. `stack.ps1 health` green including the new probe.

## 4. Phase C — research service reliability

Separate item(s) after B; each is small and independently testable.

- **C1. `delegateToCurator` has no timeout and no retry** (`research-service/index.ts:352-361`; `fetchPage` and `ghJson` both use `FETCH_TIMEOUT_MS`). With `max_concurrency=1`, a hung curator hangs every research job behind it. Add `AbortSignal.timeout(...)` and a bounded retry with backoff (connection-level errors only; a 4xx is not retried). The failure text keeps naming stage, research_key, sources and claims unwritten.
- **C2. Health data the operator already reads:** `check-openbrain-health.ps1` adds one query — `research_jobs` rows with `status='error'` in the last 24 h → a named fault line. This is a probe on existing data, not a new alerting system; the delivery fix (honesty gate + banner) already exists.
- **C3. Recovery cannot fix a bad image and says nothing about it.** `emergency-recovery.ps1` `Start-OB1Stack` (`:289`) and `Reset-OB1Stack` (`:305-307`) gain a post-`up` poll: any container in `restarting` after 60 s is logged WARN by name with `docker logs --tail 5`, and the summary line names it instead of `27/28`. Recovery still does not rebuild — rebuild is a deploy — but it must not report success over a loop.
- **C4. One deploy door for OB1 integration images:** `scripts/stack/ob1-deploy.ps1 -Service <svc>` builds from the checked-out pin, labels the image (`org.opencontainers.image.revision=<OB1 sha>`), recreates, waits for `healthy`, prints the label vs pin. `-Deployed` evidence and UPDATE-MANAGEMENT's "bump the pin → up -d" (`:37-44`, written for pulled images) both point at it. This is the written OB1 deploy procedure that does not exist today.
- **C5. Machine consumers of `status='error'`** (`agent-bridge grounding.py:151,226`, idea-refinery `index.ts:307,418`, daily-digest `research-client.ts:160`) learn the difference between "research died" and "research is real but unfiled" — the follow-up curator2's reviewer recommended. Own anchor.
- **C6. The 244 lost runs** stay an operator decision (curator2 out-of-scope, unchanged).

## 5. Phase D — hardening beyond this incident

**Harness**
- Generalise 5d into a Dockerfile lint that runs on every gitlink bump regardless of diff: every `OB1/integrations/*/Dockerfile` must copy by glob and run `deno check` (or an equivalent build-time module check). A per-file `COPY` list is a refusal with the sibling comment quoted.
- Enforce SERVICE-LIFECYCLE row 5 by machine: `check-project-configs.ps1` fails when a compose service publishes a host port and has neither a `healthcheck:` nor a `stack.ps1` `Probe`.
- Periodic container-state probe: a fifth sysadmin Scheduled Task calling `container_status` every 15 min, posting to `#sysadmin` only when the problem set changes (keyed on the set, so an unchanged loop does not repost — the check_backups lesson). Framing: uptime is watched remotely; probes are how absence gets noticed (row 5's own words).
- OWUI paste drift: `scripts/checks/check-owui-drift.ps1` compares CR-normalised SHA-256 of `owui/**` against live `webui.db` `tool`/`function` content; `manifest.csv` gains a `sha256` column replacing the stale `bytes`; runs from `stack.ps1 health` and after any OWUI upgrade. A3's `paste:` surface uses it as the `-Deployed` evidence.
- Queue verbs from the 09-05 audit: `-Unclaim` takes `-By`, calls `Assert-Claim`, refuses on terminal states; `-Requeue` can route to the developer (`anchor-confirmed`) not only to `ready-to-test`; `commit-msg` accepts tree SHAs after the `^{commit}` check fails.
- Tester environment: the harness README states that a tester of an image-shipping change needs Docker and the `open-brain` lease; a plan that needs them says so in its header so the claim is not taken by a tester who cannot execute it.

**Research plane**
- Curator and research both get compose healthchecks (research has none either); openbrain-mcp too (third gap, `:98-151`).
- Curator `/health` already reports `pool_rebuilds`; C2's query plus the healthcheck make both visible without new infrastructure.
- Curator image built with `deno check` means a missing module fails in CI-equivalent time (the gate) and again at build — two independent stops for the class that has now shipped three times.
- Consider `restart: on-failure:5` for build-context services so a broken image stops looping instead of masking itself as "running" in `docker compose ps` counts; recovery then sees `exited` and names it.

## 6. Order and dependencies

| Step | Depends on | Who |
|---|---|---|
| 1. Paste deep_research.py | nothing | operator, now |
| 2. A4 close `curatorpool` | nothing | operator (`-CloseOut`) |
| 3. A1 gate 5d | nothing | harness item |
| 4. A2 pass reads the plan | nothing (parallel with A1) | harness item |
| 5. A3 deploy state | A2 (shares `queue.ps1`) | harness item |
| 6. A5 protocol text | A1–A3 landed | same items' docs |
| 7. B curatorimg | A1 (RED/GREEN), A2 (full pass), A3 (`-Deployed`) | harness item + operator deploy |
| 8. C1–C4 | B deployed | harness items |
| 9. D | as capacity allows | harness items |

## 7. What this plan does not do

- It does not hot-patch the running curator (`docker cp pool.ts`). That dies on the next recreate, exactly as the podcast overlay did on 08-29.
- It does not build alerting in place of the delivery fixes. The two probes it adds read state that already exists.
- It does not replay the 244 lost runs.
