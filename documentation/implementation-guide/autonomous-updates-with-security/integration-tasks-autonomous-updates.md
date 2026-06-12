# Integration Tasks — Autonomous Updates with a Security & Impact Gate

**Anchored to:** [`guide-autonomous-updates.md`](guide-autonomous-updates.md)
(source of truth) and
[`integration-plan-autonomous-updates.md`](integration-plan-autonomous-updates.md)
(phased model).

**For autonomous-agent execution.** Each task is a single verifiable unit, marked
`[AGENT]` / `[OPERATOR]` / `[GATE]`. Cross-references `Guide §X` point into the
guide — read the referenced section before executing. Working directory:
`d:\Open WebUI\ai-stack` (PowerShell unless a Bash block is marked).

> **Scope note:** the orchestrator and its clients are most cleanly written in
> Deno/TypeScript so the `GoogleOAuth`/`GmailClient` and research-client code
> copies directly from `OB1/recipes/daily-digest/`. Task acceptance is stated as
> observable behaviour, not a specific language, but the examples assume Deno.

---

## Phase 0 — Spike + pre-flight

### T0.1 — Confirm research + curator reachable `[AGENT]`
- **Action:**
  ```powershell
  Invoke-RestMethod http://127.0.0.1:8818/health           # openbrain-research
  # curator has no host port by default; probe from inside obnet:
  docker exec openbrain-curator sh -c "wget -qO- http://localhost:8000/health" 2>$null
  ```
- **Acceptance:** research `/health` returns `ok:true`; curator responds. If
  either is down, **stop** — the gate is inert without them (Guide §14).

### T0.2 — Trivial end-to-end research probe `[AGENT]`
- **Action:** submit a throwaway query and poll to `done`:
  ```powershell
  $h = @{ "x-brain-key" = "<MCP_ACCESS_KEY>"; "Content-Type"="application/json" }
  $job = Invoke-RestMethod -Method Post http://127.0.0.1:8818/research -Headers $h `
    -Body '{"query":"test: docker engine latest stable version","origin":"agent","options":{"dry_run":true}}'
  # poll $job.job_id on /research/jobs/<id> until status=done
  ```
- **Acceptance:** job reaches `done` with a non-empty `result.synthesis`.
  Confirms the gate's core dependency works headlessly.

### T0.3 — Watchtower digest-pin detection spike `[AGENT]` (the core unknown, Guide §9/§16)
- **Action:** with a **digest-pinned** test container whose floating tag has
  since moved, run watchtower in monitor-only against it and observe whether it
  reports an available update or skips it as pinned. Document the result.
- **Acceptance:** a written finding — "watchtower DOES / DOES NOT report updates
  for digest-pinned refs" — that decides T3.1 (watchtower-native trigger) vs
  T3.2 (`tag-digest-detector` fallback).

### T0.4 — Inventory digest-pinned vs floating-tag services `[AGENT]`
- **Action:** list each main-stack + OB1 service's `image:` and classify
  pinned (`@sha256:`) vs floating tag vs locally-built.
- **Acceptance:** a table of candidate-managed services with their tracked tag;
  flag that only `llm-gateway` + `portal-alerter` are digest-pinned today (so
  most services need the LiteLLM §19 digest-pin treatment before they can be
  *safely auto-applied*, though they can be *detected/researched* immediately).

### G1 — Spike reviewed `[GATE]`
- **Prompt:** present the T0.3 finding + T0.4 inventory; recommend the trigger
  mechanism. Operator replies "proceed with <watchtower|detector>".

---

## Phase 1 — Mailer + research/curator clients

### T1.1 — Build the Gmail mailer `[AGENT]`
- **Action:** create `update-orchestrator/src/clients/{google-oauth,gmail}.ts`
  copied from `OB1/recipes/daily-digest/src/clients/`; default token path
  `secrets/google/update-orchestrator/token.json`, credentials =
  the shared `open-brain-email` client. Add a `setup-token.ts` clone (scope
  `gmail.send`).
- **Acceptance:** unit test sends through a mocked `messages/send`; the raw
  RFC-2822 builder + base64url encode match the source pattern.

### T1.2 — Build the research client `[AGENT]`
- **Action:** `research-client.ts` — `submit(query, options)` → job id;
  `waitDone(jobId, timeoutMs)` → result; `x-brain-key` header; base URL
  `http://openbrain-research:8000` (obnet) with `127.0.0.1:8818` host fallback.
- **Acceptance:** unit test against a mocked job lifecycle; real smoke against
  T0.2 returns a synthesis.

### T1.3 — Build the curator client `[AGENT]`
- **Action:** `curator-client.ts` — `ingestValidation(...)` and
  `ingestVulnerability(...)` posting the Guide §7 claim shapes to
  `POST /ingest/research-package`, with `kind` = `upgrade-validation-check` /
  `security-vulnerability` and the `metadata` block.
- **Acceptance:** unit test asserts the payload shape; a live smoke writes a
  test `upgrade-validation-check` claim and it is retrievable via OB search.

### G2 — Operator bootstraps the Gmail token `[GATE] [OPERATOR]`
- **Prompt:**
  > Run `setup-token.ts` for the orchestrator on the host (browser OAuth
  > consent, scope `gmail.send`), then move the resulting `token.json` to
  > `secrets/google/update-orchestrator/`. Confirm the `open-brain-email`
  > OAuth app is published to **Production** (not Testing) so the refresh token
  > doesn't expire in 7 days. Reply "token ready".
- **Acceptance:** operator replies "token ready"; agent verifies the token file
  exists (without printing it).

---

## Phase 2 — Orchestrator core (monitor-only)

### T2.1 — Scaffold the orchestrator service `[AGENT]`
- **Action:** `update-orchestrator/` (Dockerfile, `src/index.ts`): HTTP
  `POST /hook`, `POST /check/<service>`, `GET /health`, `GET /runs`, bound to
  `127.0.0.1`. Wire the three clients from Phase 1.
- **Acceptance:** `GET /health` returns ok with a self-test of research +
  curator reachability.

### T2.2 — Implement the gate (resolve → research → verdict) `[AGENT]` (Guide §5/§6)
- **Action:** on `/check/<service>`: resolve current/candidate digest+version
  from the policy manifest + registry; submit the two research jobs (security
  query incl. the "does candidate fix a CVE in current" clause; impact query
  using the `usage_profile`); run the verdict step against
  `llama-cpp ... :nothink` returning the Guide §6.4 JSON.
- **Acceptance:** `/check/<a-digest-pinned-service>` produces a structured
  verdict object in `/runs`. Fail-closed: with research stubbed unreachable, the
  verdict is `needs-human` (Guide §14), never `clear`.

### T2.3 — Ingest claims + email (research + notify only) `[AGENT]` (Guide §7/§8)
- **Action:** always ingest the `upgrade-validation-check` claim; on a security
  finding ingest `security-vulnerability` claim(s); send the decision email with
  the Guide §8 subject scheme and a pre-filled manual bump command. **No apply
  code path exists in this phase** — a `clear` verdict records
  "awaiting human go-ahead", never an apply.
- **Acceptance:** a real `/check` produces (a) a retrievable claim, (b) a
  received email, (c) a `/runs` entry whose `applied` is `false` and whose
  action is `"awaiting_approval"` / `"blocked"` / `"needs_human"`.

### T2.4 — Policy manifest scaffold `[AGENT]` (Guide §11)
- **Action:** `config/update-policy.yaml` listing the T0.4 candidate services as
  **`policy: managed`**, with DBs + locally-built images **`never`** (Guide §11),
  each `managed` entry carrying `image`, `track`, `caution`, `usage_profile`.
- **Acceptance:** orchestrator loads the manifest; `/runs` shows the resolved
  policy per checked service; the only values present are `managed` / `never`
  (there is no auto-apply value).

### T2.5 — Add orchestrator to compose `[AGENT]`
- **Action:** add the `update-orchestrator` service + its narrow Gmail/OAuth
  egress network to `docker-compose.yml` (Guide §10); digest-pin its own image;
  exclude it from its own management. Bring it up.
- **Acceptance:** `docker ps` shows it healthy; a manual `/check` of a
  digest-pinned service completes the full monitor-only loop.

---

## Phase 3 — Watchtower as detector

### T3.1 — (if spike positive) Watchtower monitor-only + notify `[AGENT]`
- **Depends on:** G1 = watchtower
- **Action:** set `WATCHTOWER_MONITOR_ONLY=true` and
  `WATCHTOWER_NOTIFICATION_URL=generic://update-orchestrator:<port>/hook` on the
  watchtower service; relax managed services' labels from
  `watchtower.enable=false` to `watchtower.monitor-only=true`; keep
  `enable=false` on `never`-policy services and the orchestrator itself.
- **Acceptance:** a forced detection POSTs to `/hook` and starts a gate run.

### T3.2 — (if spike negative) tag-digest-detector fallback `[AGENT]`
- **Depends on:** G1 = detector
- **Action:** add a poll loop in the orchestrator: per managed service,
  `docker pull <repo>:<track>` then compare `RepoDigests[0]` to the pinned
  digest; on mismatch raise the same internal "update available" event that
  `/hook` raises. Leave watchtower as-is for non-pinned services.
- **Acceptance:** moving a tracked tag causes the detector to fire a gate run
  within one poll interval.

### G3 — Operator approves watchtower reconfiguration `[GATE] [OPERATOR]`
- **Prompt:** explain the posture change (from "auto-update off everywhere" to
  "detect + notify, never apply"); list the label changes. Operator replies
  "approved".

---

## Phase 4 — Soak / S0 (research + notify, no apply)

### T4.1 — Soak `[OPERATOR]`
- **Action:** run the stack normally for the soak period; real update events
  produce emails + claims; **nothing is applied**.
- **Acceptance:** ≥ N gate runs recorded in `/runs`, each with a claim + email.

### T4.2 — Verdict-quality review `[OPERATOR]`
- **Action:** operator reviews the emails/claims: any false `clear`? noisy
  `needs-human`? Tune the verdict thresholds (gap-ratio, confidence floor,
  severity rules) per findings.
- **Acceptance:** operator is satisfied verdicts are sound, or thresholds are
  adjusted and re-soaked.

### G4 — Operator authors the real policy manifest `[GATE] [OPERATOR]`
- **Prompt:** present the recommended `managed`/`never` class defaults (Guide
  §11, Q1) — which services are in scope, with `caution` + usage profiles.
  Operator edits `config/update-policy.yaml` and replies "policy set". **No
  service enters scope (`never` → `managed`) without this; and `managed` never
  means auto — every apply still stops for a human.**

---

## Phase 5 — Apply engine + human-approval gate (S1, interim approval)

### T5.0 — Interim human-approval channel `[AGENT]`
- **Action:** add an approval surface the orchestrator requires before any apply:
  `POST /approve/<run-id>` (operator-triggered) — bound to `127.0.0.1`. A run in
  `awaiting_approval` does nothing until this is called with a matching run id;
  an unapproved run never applies.
- **Acceptance:** an `awaiting_approval` run stays held indefinitely until
  `/approve/<run-id>` is called; an unknown/expired run id is rejected.

### T5.1 — Implement the apply engine `[AGENT]` (Guide §12)
- **Action:** **only when `/approve/<run-id>` fires** for a `clear` + `managed`
  run: pre-apply backup (one-shot the service's `*-backup` sidecar if any) →
  **edit `<repo>@<D_new>` in the original git-tracked compose file → `git add` +
  `git commit -m "[auto-update] <service> <from>→<to>"` (no push)** →
  `docker compose up -d <service>` (serialized; one apply at a time) →
  health-gate poll → on failure **`git revert` the apply commit**, recreate,
  confirm health → email outcome → update the claim's `metadata.applied`.
- **Acceptance:** an approved `clear` on a test service edits the original file,
  produces a discrete `[auto-update]` commit, applies the new digest, and the
  service stays healthy; the run log + claim reflect `applied:true`. An
  **un**approved run never reaches this code.

### T5.2 — openwebui apply special-case `[AGENT]` (Guide §9/§12)
- **Action:** for `S=openwebui`, the apply recipe replicates
  `scripts/post-update-hook.sh` (stop tailscale → wait openwebui healthy →
  start tailscale → verify) after recreate.
- **Acceptance:** a simulated openwebui apply leaves both openwebui and
  tailscale healthy.

### T5.3 — Rollback drill `[AGENT]`
- **Action:** force a post-apply health failure on a test service; confirm the
  engine `git revert`s the apply commit and the service recovers on the prior
  digest.
- **Acceptance:** the apply commit is reverted, the service returns to the prior
  digest + healthy, and a `rolled_back:true` claim + email are produced.

### G5 — Apply path reviewed before it acts on real approvals `[GATE] [OPERATOR]`
- **Prompt:** present a real approved apply on a test service + the forced
  `git revert` rollback drill result. Operator replies "apply path trusted" or
  requests changes. **This authorizes the apply path to act on real
  approvals — it does not waive the per-apply go-ahead** (Guide §13: every apply
  still requires `/approve/<run-id>`).

---

## Phase 6 — Recovery + docs (three-place rule, Guide §15)

### T6.1 — emergency-recovery `[AGENT]`
- **Action:** add `update-orchestrator` to `emergency-recovery.{ps1,bat}`
  inventory; bring it up **last** (after OB1 research stack reachable) and stop
  it **early**; ensure a recovery run does not trigger applies (orchestrator
  detects "recently started" / settle delay before acting).
- **Acceptance:** a recovery run starts the orchestrator without firing an apply.

### T6.2 — stack-map + system-health + CLAUDE.md `[AGENT]`
- **Action:** add the service/network/port/dependency to
  `.claude/skills/stack-map/SKILL.md` + `references/workspace-stacks.md`; add a
  `system-health` probe; add a CLAUDE.md "stacks at a glance" mention; update
  copilot-instructions.
- **Acceptance:** `/stack-map` includes the orchestrator; `system health`
  probes it.

---

## Phase 7 — Teams-chat approval channel (S2, Guide §13/D10)

### T7.1 — Swap the interim approval trigger for the teams-chat sink `[AGENT]`
- **Depends on:** `../teams-chat-agent-orchestration/` being built + governed.
- **Action:** replace the interim `POST /approve/<run-id>` trigger (T5.0) with a
  `teams-chat` approval sink: the verdict + the exact staged file change are
  posted into the governed chat for a human go/no-go; on approval the **same
  Phase-5 apply engine** runs unchanged. Security-motivated updates are messaged
  for awareness. The human go-ahead is still required per apply — only its
  delivery channel changes.
- **Acceptance:** an approval request appears in teams-chat; the apply runs only
  after a human approves there; security updates are messaged; no apply path
  bypasses the human.

**Do not start Phase 7** until that system exists. The guide reserves the
pluggable-sink seam (D10) so this is an adapter over the Phase-5 engine, not a
rewrite — and it is the end-state delivery channel, not a new autonomy level.

---

## Phase 8 — Weekly cadence + field reports + digest loop (Guide §5A, D12–D18)

Layers the operator's timing model over Phases 2–7. Changes *when* the
recommendation/apply happen and *what* feeds the verdict — not the per-update gate.

### T8.1 — Job C: user/community field-report research `[AGENT]` (Guide §6.5/D15)
- **Action:** add a third research job per candidate — query r/OpenWebUI, the
  image's GitHub issues, and forums for reported bugs/regressions on the
  candidate version. Fold its synthesis into the validation claim (§7) and the
  verdict's `field_report_concerns` (§6.4). Optional per-service source hints in
  the manifest (issue-tracker URL).
- **Acceptance:** a `/check` of a real service returns a verdict whose
  `field_report_concerns` is populated from sourced community results (or empty
  with a `[GAP]` note when none are found); a cluster of unresolved reports
  drives `blocked`/`needs-human`.

### T8.2 — Maturity delay + eligibility `[AGENT]` (D13)
- **Action:** add `maturity_days` (default 7) to manifest entries; resolve each
  candidate's version publish date; compute `eligible_after`. A researched-but-
  ineligible candidate is **parked** (kept in `/runs`, job C re-run daily) and
  never enters the Friday queue until eligible.
- **Acceptance:** a freshly-released candidate is researched immediately but
  reported `eligible:false` until `maturity_days` elapse; a per-service override
  shortens/lengthens it.

### T8.3 — Daily detection step on the 01:00 chain `[AGENT]` (D12)
- **Action:** invoke the detection+research sweep once daily as a step in the OB1
  `pull → prune → digest` chain (`OB1/docker/cron/crontab`, 05:00 UTC) — e.g. a
  chained POST to the orchestrator — rather than reacting to watchtower's poll.
  Watchtower remains a continuous secondary feed into `/hook`.
- **Acceptance:** one daily sweep runs in the 01:00 window; `/runs` shows a dated
  daily batch of detections/research; no apply fires from the daily step.

### T8.4 — Accumulation queue `[AGENT]` (D14)
- **Action:** persist eligible candidates in a **pending-updates queue** in the
  state volume; re-evaluate parked items daily (a new field report can flip
  `clear`→`needs-human`). Expose `GET /pending`.
- **Acceptance:** eligible candidates accumulate across days; `GET /pending`
  lists the week's queue with each item's latest verdict.

### T8.5 — Friday recommendation synthesizer `[AGENT]` (D16)
- **Action:** on Friday, collect the queue + all research, evaluate feasibility
  as a batch (conflicts, ordering, caution tiers), and synthesize **one**
  recommendation presenting **GO (full) / GO (security-only) / NOGO**. Deliver
  via the Phase-7 teams-chat sink; interim, via the `🗓️` batch email (Guide §8).
- **Acceptance:** a Friday run produces a single recommendation artifact with the
  three selectable options and the per-item rationale; apply waits on the selection.

### T8.6 — Go / security-only / nogo handling `[AGENT]` (D14/D16/D17)
- **Action:** parse the human selection: **GO** applies the full `clear` set
  (serialized, §12); **security-only** applies just the security-motivated/
  blocked-by-vuln subset and rolls the rest to next Friday; **NOGO** postpones the
  whole batch to next Friday (queue persists, re-evaluated).
- **Acceptance:** each selection produces the correct apply set + the correct
  carry-over; a NOGO leaves the stack untouched and the queue intact.

### T8.7 — Urgent security override `[AGENT]` (D17)
- **Action:** when a candidate carries a high/critical `security-vulnerability`
  finding, surface it **immediately** (out-of-band `🚨` email + teams-chat), allow
  it to **bypass the maturity delay** (operator-tunable), and always include a
  **standalone security-only plan** in the next recommendation.
- **Acceptance:** an injected critical CVE finding fires an immediate urgent alert
  the same day (not held for Friday) and appears as an independently-approvable
  security-only option.

### T8.8 — Daily-digest loop + podcast gate `[AGENT]` (D18)
- **Action:** publish the day's update findings (validation + security claims) to
  the daily-digest briefing inputs; make the autonomous podcast step **wait** for
  the update-research step (and the digest's other required inputs) to complete
  before it renders — best-effort with a timeout so a slow research run can't
  indefinitely block the morning podcast. Coordinate with
  `../../daily-digests-autonomous-podcasts/`.
- **Acceptance:** the digest briefing includes the day's update findings; the
  podcast does not render until the update-research step reports complete (or the
  timeout fires, logged).

### G8 — Operator confirms the cadence + recommendation flow `[GATE] [OPERATOR]`
- **Prompt:** present a dry-run Friday recommendation (go/security-only/nogo) +
  the daily-digest entry + the urgent-security path. Confirm `maturity_days`,
  the Friday apply time, and the podcast-gate timeout. Operator replies
  "cadence approved".

---

## Appendix — Fail-closed invariant (applies to every phase)

Any error, unreachable dependency, low-confidence verdict, or high research
gap-ratio resolves to **`needs-human` + hold + email** — never a silent
`clear`, never an unreviewed apply. A gate that fails open is worse than no gate
(Guide §14).
