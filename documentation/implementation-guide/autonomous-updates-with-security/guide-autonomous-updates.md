# Guide — Autonomous Updates with a Security & Impact Gate

**Status:** Source of truth (design). Plan and task documents are generated from
this file — keep it authoritative.

**Last verified against the live stack:** 2026-06-11.

**Companion documents:**
[`integration-plan-autonomous-updates.md`](integration-plan-autonomous-updates.md)
(phased execution) ·
[`integration-tasks-autonomous-updates.md`](integration-tasks-autonomous-updates.md)
(agent-executable checklist).

---

## 1. Purpose

When a new container image (or a pinned dependency) becomes available for a
service in this workspace, applying it blindly is unsafe: the new version may
carry a **security vulnerability** (the LiteLLM CVE-2025-55182 supply-chain
compromise is the motivating example — see
[`../LiteLLM-Proxy/guide-LiteLLM-Proxy.md`](../LiteLLM-Proxy/guide-LiteLLM-Proxy.md)
§19) or a **breaking change** that silently degrades a service other parts of
the stack depend on.

This guide specifies a pipeline that, **before any upgrade is applied**:

1. Runs thorough research on the candidate update using the existing
   **`openbrain-research`** engine — two dimensions: (a) known vulnerabilities,
   and (b) impact / breaking-change analysis against how *this* stack uses the
   service.
2. Produces a structured **verdict** — `clear`, `blocked`, or `needs-human`.
3. **Ingests the result as an Open Brain claim** — the validation summary is
   marked `kind: upgrade-validation-check`; any vulnerability found is marked
   `kind: security-vulnerability`.
4. **Emails the summary** to the operator via the same Google OAuth Gmail
   mechanism the daily digest already uses.
5. **Stops and waits for a human go-ahead.** No in-scope update is applied
   without an explicit human approval delivered through the **teams-chat
   orchestration** system
   ([`../teams-chat-agent-orchestration/`](../teams-chat-agent-orchestration/)).
   On approval, the orchestrator **applies the update autonomously** — editing
   the **original, git-tracked files** (e.g. the `image:` digest in
   `docker-compose.yml`) and recreating the service. Rollback is **git**.

The trigger is **watchtower** detecting that a newer image is available. The
"autonomous" part of this system is everything *up to* the apply — detection,
three-dimensional research, the pass/stop verdict, claim ingestion, notification,
and staging the exact file change. The apply itself is **human-gated**: updates
are STOPPED until the operator gives the go-ahead via team messaging. Once
approved, the apply (file edit + recreate) and any rollback (git) run
autonomously. Security-relevant updates are messaged to the operator for
awareness regardless of decision.

**Cadence (added 2026-06-12 — see §3 D12–D18 and §5A).** Detection runs **daily**
as a step in the existing 01:00 scheduled chain; each candidate is researched on
three dimensions — security + impact + **user/community field reports** — and held
by a **maturity delay** (default 7 days) so follow-up bug-fix releases and
real-world reports can surface. Eligible updates **accumulate through the week and
are recommended as a batch on Friday** (the weekend is the buffer for any
regressions) through the teams-chat orchestration, where the operator chooses
**go / security-only / nogo**. Security vulnerabilities are flagged **urgent** and
surfaced immediately. The per-update safety gate below is unchanged — only the
*timing* of the recommendation and apply is batched.

This system is the **automation of the manual per-service digest-bump runbook**
already written for the LiteLLM gateway (LiteLLM guide §19.3). That runbook —
pull floating tag → resolve digest → check advisories → bump + recreate +
re-verify — is exactly what this pipeline performs, for every watched service,
with the advisory check upgraded to a grounded research gate.

## 2. Relationship to existing stack components

Every component this design needs **already exists and is deployed** — this is
an orchestration layer over live services, not a green-field build.

| Existing piece | Role in this design | Reference |
|---|---|---|
| **`openbrain-research`** (live, `127.0.0.1:8818` / `openbrain-research:8000` on obnet) | The vulnerability + impact research engine. Async `POST /research` → poll `GET /research/jobs/:id`. Grounded, Tor-egress, returns synthesis + cited sources + `[GAP]`s. | `OB1/integrations/research-service/`, `../research-engine-for-OB/` |
| **`openbrain-curator`** `POST /ingest/research-package` (live) | Ingests the verdict + findings as grounded **claims** with `kind`/`metadata`. Thread-aware, dedups, conflict-detects. | `OB1/integrations/research-curator/index.ts` |
| **Gmail send pattern** (`GoogleOAuth` + `GmailClient`) | The summary-email mechanism. `gmail.send` scope, OAuth refresh-token, `users/me/messages/send`. | `OB1/recipes/daily-digest/src/clients/{google-oauth,gmail}.ts` |
| **`watchtower`** (live, polls every 2 min) | The **update detector / trigger**. Reconfigured to **monitor-only** (notify, never apply). | `docker-compose.yml` (watchtower), `scripts/post-update-hook.sh` |
| **Digest-pin convention** | The deployment safety model this pipeline preserves and automates. Watched services run on pinned digests; this system performs the deliberate bumps. | LiteLLM guide §19; `portal-alerter` in `docker-compose.yml` |
| **Backup sidecars** | Pre-apply snapshots for stateful services. | `*-backup` services in `docker-compose.yml` |
| **`emergency-recovery.{ps1,bat}`** | Rollback substrate; the "three-place rule" target. | `scripts/`, CLAUDE.md |
| **Teams-chat orchestration** (designed, not built) | The channel that delivers the **required human go-ahead** before any apply (the end-state approval sink). | `../teams-chat-agent-orchestration/` |

## 3. Locked design decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Watchtower runs monitor-only — it detects and notifies, it never applies.** | Auto-apply is exactly the supply-chain risk the LiteLLM §19 hardening closes. Watchtower's value here is its registry-polling detector; the apply decision must pass the gate first. `WATCHTOWER_MONITOR_ONLY=true`. |
| D2 | **A new `update-orchestrator` service owns the gate, decision, apply, and notification.** It lives in the **main stack** (with watchtower) because it needs `docker.sock`. | The privileged "drive compose" capability already lives in the main stack (watchtower, recovery scripts). Keeping the orchestrator there avoids granting OB1 (a separate project) docker.sock. |
| D3 | **The gate is two research jobs, not one:** (A) security/vulnerability, (B) impact / breaking-change vs this stack's usage. Both must clear. | "Clear to update" means *both* "no new vuln" *and* "won't break dependents." They are different questions with different sources. |
| D4 | **The verdict is a structured object** (`clear` / `blocked` / `needs-human` + rationale + blocking findings), produced by a verdict step over the two syntheses. | A pass/stop automation needs a machine-readable decision, not prose. Grounded claims feed the decision; the decision is auditable. |
| D5 | **Every run ingests a claim.** Validation summary → `kind: upgrade-validation-check`. Each vulnerability → `kind: security-vulnerability`. Marked with service, from/to version, digest, decision in `metadata`. | The operator's requirement: the research outcome is durable knowledge in Open Brain, not an ephemeral log line. `volatility: fast` so security findings re-validate. |
| D6 | **Every run emails a summary** via a dedicated `gmail.send` token (`secrets/google/update-orchestrator/`), reusing the daily-digest `GoogleOAuth`/`GmailClient` pattern — **not** a hard dependency on `portal-alerter` (which is profile-gated). | The operator must have awareness of every decision, especially blocks and security-motivated updates. Independent token = independent revocation. |
| D7 | **Apply edits the original, git-tracked files in place** (e.g. the `image:` digest in `docker-compose.yml`) — *not* a side-car override file. **Rollback is git** (`git revert`/`checkout` of the apply commit) followed by a container recreate. One service at a time, health-gated. | Operator directive: updates impact the real files. Every file is git-tracked, so git is both the audit trail and the rollback. Still a deliberate digest bump — never floating-tag drift. |
| D8 | **Per-service scope is `managed` or `never`.** `managed` = research-gated **and human-approved (via teams-chat) before apply**. There is **no** apply-without-human-approval path. `never` = out of scope (stateful DBs, locally-built images). | Operator directive: updates are STOPPED until a human gives the go-ahead. Autonomy covers research + staging; the apply is always human-approved. DBs / locally-built images don't fit the registry-tag + git-bump model at all. |
| D9 | **The apply capability is gated on a human go-ahead delivered via teams-chat** (`../teams-chat-agent-orchestration/`). Until that channel exists the system runs **research + claim + email + STOP** — it applies nothing; the email carries a pre-filled manual bump command for the operator to apply by hand if they choose. | Operator directive. The research/notify value lands immediately at zero apply-risk; the apply path lights up only when the human-approval channel is wired (or via the interim manual approval of §13). |
| D10 | **The approval sink is human-in-the-loop, delivered via teams-chat.** It is pluggable only to allow an **interim approval mode** (manual operator trigger / email) while teams-chat is unbuilt. There is **no** autonomous-apply sink. | The operator's end-state is human approval via team messaging, security updates messaged for awareness. The pluggable seam keeps the interim manual path and the future teams-chat path as a config choice, not a rewrite — but neither is ever auto. |
| D11 | **Watched services are digest-pinned**; the tracked floating tag is recorded per service for *detection only*. | Detection compares "what the floating tag points to now" against "the pinned digest we run." Deployment stays on the immutable digest. See §9 for the watchtower/digest-pin interaction. |
| D12 | **Detection runs daily on the existing 01:00 scheduled chain**, not on watchtower's 2-min poll. Watchtower stays monitor-only as a continuous secondary feed; the authoritative once-daily sweep is a step in the OB1 `pull → prune → digest` cron (`OB1/docker/cron/crontab`, 05:00 UTC = 01:00 EDT). | One off-peak batch/day aligns detection with the slice that already runs the digest, and lets the day's findings feed it (D18). |
| D13 | **Maturity delay — a candidate is not *eligible* until its version has been published ≥ `maturity_days` (default 7).** Detection + research happen immediately; only *eligibility for recommendation/apply* is age-gated. Per-service override; urgent security may bypass (D17). | A fresh release often gets a fast follow-up bug-fix; ~1 week lets those land and lets field reports (D15) accumulate before we touch the stack. |
| D14 | **Weekly accumulation with a Friday apply window.** Eligible updates queue through the week; the recommendation + apply happens **Friday**, with the weekend as the regression buffer. NOGO postpones the whole batch to the next Friday. | A single, predictable weekly change window with slack to fix anything that breaks — not mutating the stack on random weekdays. |
| D15 | **A third research dimension — user/community field reports** (Open WebUI subreddit, GitHub issues, forums) for reported bugs/regressions on the candidate. Joins job A (security) + job B (impact). | Real-world breakage often shows up in community reports before changelogs; the maturity delay (D13) is what makes those reports exist to find. |
| D16 | **The Friday recommendation is synthesized and delivered through the teams-chat orchestration** (PM-composed), presenting selectable options: **GO (full) / GO (security-only) / NOGO**. Apply waits on the human selection. | The batched form of the S2 human-approval sink (D10): one weekly, whole-picture recommendation, not N per-event prompts. |
| D17 | **Security vulnerabilities are urgent.** A high/critical `security-vulnerability` finding (esp. `security_motivated` — running version known-vulnerable) is surfaced **immediately** (out-of-band email + teams-chat), may **bypass the maturity delay** (operator-tunable), and is **always offered as a standalone "security-only" plan** approvable on its own. | A known-exploited CVE should not wait for Friday or a 7-day soak; and the operator must be able to ship *only* the security fixes without taking the rest of the batch. |
| D18 | **The daily digest loops in the day's update findings; the podcast is gated on completion.** Update-validation + security claims become inputs to the digest briefing; the autonomous podcast step does **not** render until all required inputs (incl. the update-research step) are complete for that cycle. | Operator directive: "always delay podcasts until all requirements are met." Reuses the digest's claim-based briefing (ON = pure audio renderer, digest-podcast-services D5). |

## 4. Architecture

```
                                    registry (ghcr.io / docker.io)
                                              ▲ poll floating tags
                                              │ (monitor-only)
                                   ┌──────────┴───────────┐
                                   │      watchtower      │  D1: detects, never applies
                                   │   MONITOR_ONLY=true  │
                                   └──────────┬───────────┘
                                              │ "update available" notification
                                              │ (shoutrrr generic webhook)
                                              ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │                        update-orchestrator (NEW, main stack)            │
   │  has docker.sock (D2) · holds per-service update policy (D8)            │
   │                                                                         │
   │  1. resolve current→candidate (digest D_old → D_new, versions)          │
   │  2. submit 2 research jobs ───────────────►  openbrain-research :8818    │
   │       A. security/vuln   ◄───────────────── grounded synthesis+sources  │
   │       B. impact/breaking ◄─────────────────                             │
   │  3. verdict step (llama-cpp) → {clear|blocked|needs-human, findings}     │
   │  4. ingest claims ────────────────────────►  openbrain-curator           │
   │       upgrade-validation-check (+ security-vulnerability on block)       │
   │  5. email summary ────────────────────────►  Gmail API (gmail.send)      │
   │  6. STOP for human go-ahead  ──────────────►  teams-chat (S2) / email   │
   │       on approval + clear → edit ORIGINAL files, git commit, recreate,  │
   │                             health-gate, git-revert on fail (D7)         │
   │       blocked / needs-human / no approval → HOLD + notify               │
   └───────────────────────────────────────────────────────────────────────┘
                                              │ apply (git edit+commit + recreate)
                                              ▼
                                   the watched service container
```

The orchestrator is the only new long-running component. Everything it talks to
(research, curator, Gmail, docker) already exists.

## 5. The update lifecycle (end-to-end)

For one watched service `S` when watchtower reports an available update:

1. **Detect.** Watchtower (monitor-only) finds the floating tag `T` for `S` now
   resolves to digest `D_new`, different from the running pinned digest
   `D_old`, and POSTs a notification to the orchestrator's webhook.
2. **Resolve context.** Orchestrator reads `S`'s entry from the **update-policy
   manifest** (§11): its image repo, tracked tag `T`, policy class, and a short
   **usage profile** (how the stack depends on `S` — used to focus impact
   research). It resolves `D_old`→version and `D_new`→version (image labels /
   registry metadata).
3. **Research — security (job A).** `POST /research` with a query like:
   *"Known security vulnerabilities, CVEs, and supply-chain advisories for
   `<image>` at version `<candidate>`; and any CVE in `<current>` that
   `<candidate>` fixes."* (The second clause surfaces **security-motivated
   updates** — see §6.3.)
4. **Research — impact (job B).** `POST /research` with a query like:
   *"Breaking changes, removed/renamed config, default-behavior changes, and
   migration notes between `<image>` `<current>` and `<candidate>`, focused on:
   `<usage profile>`."*
5. **Poll** both jobs to terminal state (bounded; research has its own wall
   clock). Collect each `synthesis`, `cited_sources`, `gaps`, `metrics`.
6. **Verdict (§6.4).** A verdict step over both syntheses emits a structured
   decision. `[GAP]`-heavy or low-confidence research → `needs-human`, never a
   silent `clear`.
7. **Ingest claims (§7).** Always write the `upgrade-validation-check` claim;
   on a security finding, also write `security-vulnerability` claim(s).
8. **Email (§8).** Send the summary; subject encodes the decision.
9. **STOP for human approval (§11/§12).** Present the verdict + the exact staged
   file change to the human — via teams-chat (S2) or email (interim) — and
   **wait**. On a go-ahead for a `clear`/`managed` service, apply: edit the
   original files → `git commit` → recreate → health-gate → `git revert` on
   failure. On `blocked`/`needs-human`/no approval, hold.
10. **Audit.** Append a run record (service, versions, digests, decision,
    claim ids, job ids, apply outcome) to the orchestrator's run log.

> **Cadence note (§5A):** steps 1–8 run **daily** for newly-detected candidates;
> step 9 (recommend + apply) is **batched to Friday** and the apply waits on the
> human's go / security-only / nogo selection. Urgent security findings (D17)
> break the batch and surface immediately.

## 5A. Cadence, accumulation & the weekly batch (daily detect → Friday recommend)

This section specifies the timing model layered over §5 (D12–D18). It changes
*when* the recommendation and apply happen; it changes **nothing** about the
per-update gate (§6–§14) — every update is still individually researched,
claim-backed, human-approved, health-gated, and git-revertible.

**Daily (01:00 EDT / 05:00 UTC)** — a step in the existing OB1 cron chain
(`pull → prune → digest`, `OB1/docker/cron/crontab`):

1. **Detect** available updates (D12): the watchtower monitor-only feed plus a
   `tag-digest-detector` sweep over the `managed` set.
2. For each *newly-detected* candidate, run the **three research jobs** — A
   (security), B (impact), **C (user/community field reports, §6.5)** — and
   **ingest claims** (§6/§7), exactly as today.
3. **Maturity filter (D13):** mark a candidate `eligible` once
   `version_publish_date + maturity_days` (default 7) has passed. Ineligible
   candidates stay researched-but-parked; their job-C research keeps refreshing
   as more field reports land during the wait.
4. **Enqueue** eligible candidates into the week's **pending-updates queue**
   (persisted in the orchestrator state volume alongside the run log).
5. **Feed the digest (D18):** the day's update findings (validation + any
   security claims) are handed to the daily-digest briefing; the autonomous
   podcast step is **gated** — it does not render until the update-research step
   (and the digest's other required inputs) have completed for that cycle.
6. **Urgent security (D17):** if any candidate carries a high/critical
   `security-vulnerability` finding, surface it **immediately** (out-of-band
   email + teams-chat) — do not wait for Friday.

**Friday (same 01:00 chain, Friday branch):**

1. **Collect** the week's accumulated eligible candidates + all their research
   (security / impact / field-report syntheses + claims).
2. **Evaluate feasibility as a batch** — cross-service conflicts, apply ordering
   (caution tiers, §11), and which items are `clear` vs `blocked`/`needs-human`.
3. **Synthesize a recommendation** and post it through the **teams-chat
   orchestration** (D16), PM-composed, presenting three selectable options:
   - **GO (full)** — apply the full `clear` set.
   - **GO (security-only)** — apply only the security-motivated / blocked-by-vuln
     subset (the standalone security plan of D17); the rest roll to next Friday.
   - **NOGO** — postpone the whole batch to next Friday.
4. **STOP and wait** for the human's selection (the §13 S2 gate, batched).
5. **On GO / security-only:** apply the selected set **serialized + health-gated**
   (§12), within the Friday→weekend window so the weekend is the buffer to catch
   and `git revert` any regression.
6. **On NOGO:** postpone; the queue **continues to accumulate** and re-evaluates
   next Friday as more field reports arrive (a parked item may flip
   `clear`→`needs-human` if new field reports surface a regression).

```
 Mon ─ Tue ─ Wed ─ Thu ─── FRIDAY ─────────── Sat · Sun
  └ daily: detect → research(A/B/C) → maturity-park → enqueue → feed digest
                                       │
                    (urgent security → immediate teams-chat, any day)
                                       ▼
                        collect week · feasibility · PM recommendation
                          → teams-chat: GO / SECURITY-ONLY / NOGO
                                       │ GO
                                       ▼
                        serialized health-gated apply ──► weekend = regression buffer
```

## 6. The security & impact research gate

### 6.1 Why three research dimensions

A "clear to update" verdict requires answering three independent questions:

- **Security (job A):** does the candidate introduce a known vulnerability (and
  does it fix one the running version has)?
- **Impact (job B):** will the candidate break or change behavior that a
  dependent service relies on?
- **Field reports (job C, D15):** are real users reporting bugs/regressions on
  this candidate version (Open WebUI subreddit, GitHub issues, forums)?

A single query blurs them; the sources differ (NVD / advisories / GitHub
security vs changelogs / release notes / migration guides vs community threads
and issue trackers). D3 + D15 keep them separate so the verdict can cite each
independently. The maturity delay (D13) is what gives job C something to find —
field reports accrue in the days after a release, which is exactly why we wait.

### 6.2 Invoking `openbrain-research`

Per the live contract (`OB1/integrations/research-service/`):

```
POST http://openbrain-research:8000/research      # (host: 127.0.0.1:8818)
  Headers: x-brain-key: <MCP_ACCESS_KEY>, Content-Type: application/json
  Body: { "query": "...", "origin": "agent",
          "options": { "confidence_floor": 0.60, "disable_web_search": false } }
  → 202 { "job_id": "<uuid>", "status": "queued" }

GET /research/jobs/<job_id>   (poll until status ∈ {done, error, cancelled})
  → result.synthesis, result.cited_sources, result.gaps, metrics.gap_ratio …
```

The engine routes web fetches through Tor and enforces grounding (ungrounded
claims are never stored or returned), so the gate inherits the stack's
privacy + no-hallucination guarantees for free.

### 6.3 Security-motivated updates (the inverse case)

The operator requirement *"security updates should be messaged to the human"*
is handled by job A's second clause: research the **currently-running** version
for known CVEs. If the running version is vulnerable and the candidate fixes it,
the run is tagged `security_motivated: true`. These are surfaced with a
higher-priority email subject and (future) a teams-chat alert, even when the
policy would otherwise hold for human approval — a known-vulnerable running
version is itself a finding worth the operator's attention.

### 6.4 The verdict step

The orchestrator passes both syntheses (+ gap ratios + cited sources) to a
verdict prompt against `llama-cpp` (local, `:nothink` for speed) that must
return a structured object:

```json
{
  "decision": "clear | blocked | needs-human",
  "security_motivated": false,
  "severity": "none | low | medium | high | critical",
  "blocking_findings": [ { "summary": "...", "source": "https://..." } ],
  "field_report_concerns": [ { "summary": "...", "source": "https://..." } ],
  "rationale": "one paragraph, citing the three syntheses",
  "confidence": 0.0,
  "eligible_after": "<ISO date — version_publish + maturity_days (D13)>"
}
```

Decision rules (encoded, not left to prose):

- Any **high/critical** vulnerability in the candidate with no mitigation →
  `blocked`.
- Any **breaking change** that touches the service's usage profile →
  `blocked` (or `needs-human` if ambiguous).
- A **cluster of unresolved field-reported regressions** (D15, job C) on the
  candidate → `blocked` (or `needs-human` if the reports are thin/contested).
- Research `gap_ratio` above a threshold, or verdict `confidence` below a
  floor → `needs-human` (never auto-clear on thin evidence).
- Otherwise → `clear`.

The verdict is **advisory input to the human decision** (§11), not the final
action: a `clear` verdict on a `managed` service still STOPS for a human
go-ahead before anything is applied. **Eligibility is separate from the verdict**
— a `clear` update is not *recommended* until it is `eligible` (maturity delay
D13) and the **Friday** window (D14, §5A); urgent security findings (D17)
override both the delay and the batch.

## 7. Claim ingestion

Each run writes durable knowledge to Open Brain via the curator
(`POST /ingest/research-package`, `x-brain-key` auth):

**Always — the validation claim:**

```json
{
  "research_key": "upgrade-validation:<service>:<to_version>",
  "query": "Upgrade validation for <service> <from_version> → <to_version>",
  "claim": "<service> <to_version>: <decision> — <one-line rationale>",
  "synthesis": "<combined security + impact synthesis, [SOURCED]/[INFERRED] tagged>",
  "kind": "upgrade-validation-check",
  "volatility": "fast",
  "revalidate_days": 7,
  "sources": [ … cited_sources from both jobs … ],
  "metadata": {
    "service": "<service>", "image": "<repo>",
    "from_version": "…", "to_version": "…",
    "from_digest": "sha256:…", "to_digest": "sha256:…",
    "decision": "clear|blocked|needs-human",
    "security_motivated": false, "severity": "…",
    "applied": true|false
  }
}
```

**On a security finding — one `security-vulnerability` claim per CVE/advisory**,
with `metadata.external_id` = CVE id, `severity`, the affected version range,
and whether the candidate or the running version is affected. `volatility: fast`
so the claim re-validates and doesn't silently go stale.

This means: a clean upgrade leaves a "validated, clear" trail; a blocked
upgrade leaves both the validation verdict **and** the vulnerability itself as
searchable, grounded claims (`memory-stack-routing` / `search` will surface them
later).

### 6.5 Research dimension C — user/community field reports (D15)

Job C runs alongside A and B for each candidate: a `POST /research` query like
*"User-reported bugs, regressions, and breakage with `<image>` `<candidate>` —
check the Open WebUI subreddit (r/OpenWebUI), the project's GitHub issues, and
community forums; are people reporting problems after upgrading to this
version?"* The engine's Tor-routed web search reaches Reddit / GitHub / forum
threads; grounding still applies (ungrounded claims are dropped), so a field
report only counts if it's sourced.

Job C is **why the maturity delay exists** (D13): on release day there is nothing
to find, but over the following ~week the regression reports (or the reassuring
absence of them) accumulate. The orchestrator **re-runs job C on each daily pass
while a candidate is parked**, so the Friday recommendation reflects the *latest*
field signal, not the release-day snapshot. A field-report synthesis is ingested
into the validation claim (§7) and feeds the verdict's `field_report_concerns`
(§6.4); a cluster of unresolved reports blocks or escalates the candidate.

## 8. Email notification mechanism

A small mailer bundled into the orchestrator, copied from the proven
`OB1/recipes/daily-digest/src/clients/` pattern:

- **Auth:** `GoogleOAuth` reading `credentials.json` (the shared
  `open-brain-email` OAuth client) + a **dedicated** `token.json` under
  `secrets/google/update-orchestrator/` (scope `gmail.send`, bootstrapped once
  with a `setup-token.ts` clone — independent revocation per D6).
- **Send:** `GmailClient.sendHtml({from, to, subject, htmlBody})` →
  `POST https://gmail.googleapis.com/gmail/v1/users/me/messages/send`.
- **Egress:** the orchestrator needs outbound to `oauth2.googleapis.com` +
  `gmail.googleapis.com` only — mirror the `notify-net` pattern used by
  `portal-alerter` (a narrow egress network), **not** broad internet access.
- **Subjects encode the decision** so the inbox is triage-able:
  - `🗓️ [weekly update recommendation] <N> updates — go / security-only / nogo` (the **Friday batch**, D14/D16 — the primary operator touchpoint)
  - `🚨 [URGENT security] <service> <to_version>` (D17 — immediate, breaks the batch)
  - `⛔ [blocked: <severity>] <service> <to_version>` (vulnerability/break)
  - `🟡 [needs approval] <service> <to_version>` (clear but human-gated, interim per-event mode)
  - `🔐 [security update available] <service> <to_version>` (security_motivated)
- **Cadence (D12/D14/D18):** per-event "would-apply" emails are largely **folded
  into the daily digest** (D18) and the **Friday weekly recommendation** (the
  `🗓️` subject), so the operator gets one daily summary + one weekly decision
  rather than an email per detected update. **Blocks and urgent security
  (`🚨`/`⛔`) still send immediately**, independent of the batch.
- **Body:** decision + rationale, the three syntheses (collapsed), cited sources,
  the claim ids written, and — for `needs-human` — the exact bump command the
  operator would run to apply manually (the LiteLLM §19.3 recipe, pre-filled).

> **Why not just call `portal-alerter`?** It is profile-gated (`profiles:
> [internet, local-test]`) — not part of a default `up`. The orchestrator must
> notify even when the Portal is down, so it owns its own sender. If the Portal
> is up, the orchestrator's blocked/security emails MAY *also* fan out through
> `portal-alerter`'s `/alert` for the operator's existing alert path — optional,
> not required.

## 9. Watchtower reconfiguration & the digest-pin interaction

This is the **subtle part**, because §19 of the LiteLLM guide deliberately
**excluded** services from watchtower and **digest-pinned** them. Reconciling:

- **Monitor-only, not disabled.** Watchtower is switched to
  `WATCHTOWER_MONITOR_ONLY=true` and configured to **notify** the orchestrator
  (shoutrrr generic webhook → orchestrator `/hook`). It detects and reports; it
  never pulls-and-restarts. The universal `com.centurylinklabs.watchtower.enable=false`
  labels are **relaxed to monitor-only** (`watchtower.monitor-only=true`) for
  the services placed under management, and kept as `enable=false` for those
  explicitly out of scope (`never` policy).
- **The digest-pin detection problem.** A container deployed from
  `image@sha256:…` has no floating tag for watchtower to compare against, so a
  digest-pinned container can be treated as "pinned / nothing to do."
  **Resolution (D11):** detection tracks the *floating tag* recorded in the
  policy manifest, decoupled from the deployed digest. Two viable mechanisms,
  to be chosen at the P0 spike:
  1. **Watchtower against tag-tracking refs** — feasible if watchtower will
     report "newer digest available for `repo:tag`" while the container runs a
     digest; **must be validated** (watchtower's behavior with digest refs is
     the #1 implementation unknown — P0 spike).
  2. **A thin `tag-digest-detector`** in the orchestrator (fallback) — on
     watchtower's poll cadence (or its own), for each watched service:
     `docker pull <repo>:<tag>` then compare `RepoDigests[0]` to the pinned
     digest in compose; on mismatch, raise the same internal "update available"
     event. This does not depend on watchtower's digest behavior at all.

  The design treats watchtower as the **preferred** trigger (honoring the
  operator's "triggered by watchtower" requirement) with the detector as the
  guaranteed fallback so the pipeline is correct regardless of how watchtower
  handles digest refs.
- **The existing `post-update-hook.sh`** (tailscale reattach after an openwebui
  update) becomes irrelevant for managed services *while in monitor-only*
  (watchtower won't recreate). When the **orchestrator** applies an openwebui
  update, it must replicate that hook's logic (restart tailscale, wait for
  openwebui healthy) as part of `S=openwebui`'s apply recipe. Captured in §12.

## 10. The `update-orchestrator` service

A new main-stack service (Deno, matching the OB1 recipe toolchain so the Gmail +
research clients copy over cleanly).

| Aspect | Value |
|---|---|
| **Image / build** | Local build (`docker/Dockerfile`), pinned like other local images. |
| **Networks** | `default` (reach `127.0.0.1` host ports is via the gateway; prefer joining **`obnet`** to reach `openbrain-research:8000` + `openbrain-curator:8000` by DNS) + a narrow **`notify-net`-style** egress for Gmail/OAuth only. **Not** on `llm-net`-internal alone — it needs the OB1 services and Google. |
| **Mounts** | `docker.sock` (to recreate containers / run compose); the repo working tree **read-write + git** (to edit the original `image:` digests in `docker-compose.yml` / OB1 compose and **commit** the apply per D7); `secrets/google/update-orchestrator/` (token); a state volume for the run log + policy manifest. |
| **HTTP** | `POST /hook` (watchtower notification sink), `POST /check/<service>` (manual trigger), `GET /health`, `GET /runs` (audit). Bound to `127.0.0.1` only. |
| **Secrets** | `MCP_ACCESS_KEY` (research + curator), Gmail OAuth client+token, `DIGEST_TO`/`DIGEST_FROM`. |
| **Security posture** | `no-new-privileges`; docker.sock is the blast radius (§14) — treat the orchestrator as a privileged service, digest-pin it, exclude it from its own management, and keep its egress narrow. |

Applying an update edits the **original git-tracked files in place** (D7): the
orchestrator rewrites the service's `image: …@sha256:…` digest in
`docker-compose.yml` (or the relevant OB1 compose / config file), **commits**
that change as a discrete git revision (dedicated branch or a tagged
`[auto-update]` message), then recreates the single service. Rollback is
`git revert` of that commit + recreate. Because every apply is a normal git
revision, the operator sees all applied updates in `git log` / `git diff` and
can revert any of them by hand. The orchestrator **commits before it recreates**,
so a crash mid-apply leaves a clean, recoverable git state — never an
uncommitted partial edit. (Per workspace git etiquette the orchestrator commits
but does **not** push; the operator pushes if/when they choose.)

## 11. Per-service update policy (the safety manifest)

A declarative manifest (`config/update-policy.yaml`) is the spine of D8/D9. One
entry per watched service:

Only two policy values exist (D8): **`managed`** (research-gated, then
**human-approved via teams-chat** before the apply) and **`never`** (out of
scope). There is no auto-apply value.

```yaml
services:
  smolcrawl-pipelines:
    image: <repo>
    track: ":latest"            # floating tag watched for detection (D11)
    policy: managed             # research → human approval (teams-chat) → apply
    caution: low                # stateless leaf; trivially git-reverted
    usage_profile: "deep_research pipeline runner; OWUI-facing on app-net"
  llm-gateway:
    image: ghcr.io/berriai/litellm
    track: ":main-stable"
    policy: managed
    caution: high               # holds master key + virtual keys — extra scrutiny
    usage_profile: "OpenAI-compat gateway; holds master key + virtual keys"
  openbrain-db:
    policy: never               # stateful DB — out of scope (operator-run migration)
  tailscale:
    policy: managed
    caution: high               # network identity / netns; apply replicates post-update-hook
```

**Default policy by service class.** `caution` does not change *whether* a human
approves (always, for `managed`) — it tunes how much context the email/teams-chat
request surfaces and the apply ordering.

| Class | Examples | Default | Caution |
|---|---|---|---|
| Stateless leaf / pipeline | `smolcrawl-pipelines`, `searxng`, `mcpo`, `redis` | `managed` | low |
| Key / identity / netns | `llm-gateway`, `tailscale`, `authelia`, `caddy` | `managed` | high |
| Stateful data store | `openbrain-db`, `llm-gateway-db`, `surrealdb`, `*-data` | `never` | — (operator-run, own migration care) |
| Locally-built images | `mnemory`, `little-coder`, `gateway`, this orchestrator | `never` | — (no registry tag; updated by rebuild, not this pipeline) |

The manifest is operator-owned. The agent **never** brings a service into scope
(`never` → `managed`) autonomously — that is a deliberate operator edit. And
`managed` never means auto: it always stops for a human.

## 12. Apply + health-gated rollback

Apply runs **only after a human go-ahead** (via teams-chat, or the interim
manual approval of §13) on a `managed` service whose verdict was `clear`:

1. **Pre-apply backup** for any service with a backing volume — one-shot the
   relevant `*-backup` sidecar (the LiteLLM plan's Phase-0 pattern).
2. **Maintenance check** — skip/defer if a long-running workload is in flight
   (mirror the LiteLLM G0 check; e.g. don't recreate `llama-cpp` mid-inference).
3. **Edit + commit** — rewrite the service's `image: <repo>@<D_new>` digest in
   the **original git-tracked file** (§10) and commit it as a discrete
   `[auto-update] <service> <from>→<to>` revision (no push).
4. **Recreate** the single service (`docker compose up -d <service>`), serialized
   — never more than one managed apply at a time.
5. **Health-gate** — poll the service's healthcheck for a bounded window. On
   `openwebui`, additionally run the `post-update-hook.sh` tailscale-reattach
   logic (§9).
6. **Rollback on failure** — `git revert` the apply commit (restoring `D_old`),
   recreate, confirm health restored, and **email a rollback notice** + ingest a
   `kind: upgrade-validation-check` claim with `applied:false, rolled_back:true`.
7. **Record** the outcome in the run log; update the validation claim's
   `metadata.applied`.

Rollback is `git revert` to the prior committed state — which is why both
digest-pinning (D11) **and** git-tracking (D7) are hard prerequisites: there is
always an exact, known-good revision to return to, visible in `git log`.

## 13. Autonomy phasing

There is **no fully-autonomous-apply stage** — the human go-ahead is required at
every stage. The stages differ only in *how the human approval is delivered*.

| Stage | Behaviour | Gate to advance |
|---|---|---|
| **S0 — Research + notify + STOP** | Detect, research, verdict, ingest claims, email. **Apply nothing.** The email carries a pre-filled manual bump command so the operator can apply by hand. This is the permanent posture until an approval channel is wired. | Operator reviews real emails/claims and trusts the verdicts. |
| **S1 — Human-approved apply, interim channel** | The apply engine is live (edit original files → commit → recreate → health-gate → git-revert on fail). Approval is delivered by an **interim manual operator action** (e.g. hitting `/approve/<run-id>`, or replying to the email). On approval → apply. Still never auto. | `../teams-chat-agent-orchestration/` is built and governed. |
| **S2 — Human-approved apply via teams-chat (D10)** | The go/no-go is delivered through the **governed teams-chat** system instead of the interim trigger; security-motivated updates are messaged for awareness. | — (end state) |

S1's apply engine can be built and validated (with the operator personally
approving each apply) **before** teams-chat exists; S2 then swaps the approval
*delivery* from the manual trigger to teams-chat. The guide reserves the
pluggable-sink seam (D10) so S1→S2 is an adapter change, not a rewrite. An
operator who wants zero apply before teams-chat simply stays at S0.

## 14. Security model / threat considerations

- **docker.sock is the blast radius.** The orchestrator can recreate any
  container — it is as privileged as watchtower. Mitigations: digest-pin the
  orchestrator, exclude it from managing itself, narrow its egress to
  Google-OAuth only, bind its HTTP to `127.0.0.1`, `no-new-privileges`, and
  keep its image local-built + reviewed.
- **The gate must fail closed.** Research engine down, curator down, verdict
  low-confidence, or any unhandled error → **`needs-human` + hold + email**,
  never a default `clear`. A gate that fails open is worse than no gate.
- **The orchestrator must never apply to itself, to watchtower, or to the
  research/curator/DB services** it depends on — circular self-mutation. These
  are `never` by class (§11), even though a human approves every other apply.
- **Claim integrity.** Ingested vulnerability claims are grounded (curator drops
  ungrounded claims), so the security record can't be poisoned by hallucinated
  CVEs.
- **Email is the human's awareness channel** and must be best-effort
  independent of apply success (send before acting on `blocked`; send the
  rollback notice even if rollback itself is messy).

## 15. Three-place rule, recovery, and stack-map integration

Adding `update-orchestrator` (and reconfiguring `watchtower`) is a
container-topology change, so per CLAUDE.md it touches three places together:

1. **Compose** — `docker-compose.yml` (orchestrator service + watchtower env
   change + the `notify-net`-style egress network). Managed `image:` digests
   live in the **original** compose files (main + OB1) and are edited in place +
   committed at apply time (D7), not in a side-file.
2. **Recovery scripts** — `scripts/emergency-recovery.{ps1,bat}` service
   inventory + startup/shutdown ordering (orchestrator starts after the OB1
   research stack is reachable; stops early). The recovery stack must **not**
   trigger applies — the orchestrator should detect "recovery in progress" (or
   simply be brought up last, after a settle delay).
3. **Stack-map** — `.claude/skills/stack-map/SKILL.md` +
   `references/workspace-stacks.md` (new service, network, port, dependency
   order). The `/stack-map` drift check covers this.

Also: `modules/system-health` gains an orchestrator probe; CLAUDE.md "stacks at
a glance" gains a row.

## 16. Risks & rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| Watchtower won't detect updates for digest-pinned containers | **High (the core unknown)** | P0 spike validates; `tag-digest-detector` fallback (§9) makes the pipeline correct regardless. |
| A human-approved update breaks a service despite a `clear` verdict | Medium | Health-gated apply + `git revert` rollback to the prior digest (§12); one service at a time; backups first. The human approved it, but the machine still guards the apply. |
| Gate fails open (researches nothing, emits `clear`) | Low but severe | Fail-closed rule (§14): any error/low-confidence/high-gap → `needs-human` + hold. |
| Research engine load (2 jobs per update × N services) saturates the GPU | Medium | Serialize gate runs; reuse-heavy research is cheap; respect research's own wall-clock; batch detection, not applies. |
| docker.sock compromise via the orchestrator | Low / severe | §14 hardening; treat as a privileged service; digest-pin + narrow egress + local build. |
| Orchestrator edits the original compose and corrupts it | Medium | The edit is **committed as a discrete git revision before recreate** (D7/§10); `git revert` / `git checkout` restores it; a crash leaves a clean git state, never an uncommitted partial edit. |
| OAuth refresh-token 7-day expiry (digest of the known digest-OAuth issue) | Medium | Publish the OAuth app to Production (see `daily-digest-oauth-7day-expiry` memory) so the orchestrator's token doesn't die weekly. |
| Self-mutation loop (orchestrator updates itself / its deps) | Low | Hard exclusion list (§14); these services are `never`. |

## 17. Open questions for the operator

- **Q1 — Management scope.** Which services are `managed` (in scope for the
  research-gated, human-approved pipeline) vs `never`? Recommended: all
  registry-pulled services `managed`; stateful DBs + locally-built images
  `never`. (There is no auto-apply tier to choose — every `managed` service is
  human-approved.)
- **Q2 — Email recipient & cadence.** Reuse `DIGEST_TO`? One email per
  decision, or a batched digest of the day's update activity (blocks/security
  always immediate)?
- **Q3 — Apply mechanism. (RESOLVED — D7.)** Edit the original git-tracked files
  in place and commit each apply as a discrete `[auto-update]` revision (no
  push); rollback is `git revert`. Operator directive — supersedes the earlier
  override-file option.
- **Q4 — Watchtower vs custom detector.** After the P0 spike: keep watchtower as
  the trigger if it reports digest updates, or run the `tag-digest-detector` as
  the primary and keep watchtower only for non-pinned services?
- **Q5 — Locally-built images.** `mnemory`, `little-coder`, `gateway`, OB1
  services are built, not pulled — out of this pipeline's registry-tag model.
  Do we want a parallel "base-image / dependency" gate for *their* Dockerfiles
  (FROM lines, pinned deps), or is that a separate future effort?
- **Q6 — Scope of the impact research.** How rich should each service's
  `usage_profile` be? Richer profiles = better breaking-change detection but
  more manifest maintenance.
- **Q7 — Maturity delay (D13).** Confirm the 7-day default; which services want
  a longer soak (high-caution: `tailscale`, `llm-gateway`) or shorter? And the
  policy when an urgent security fix lands inside the window — auto-bypass the
  delay (D17), or still require the operator to opt in on the immediate alert?
- **Q8 — Friday window specifics (D14/D16).** Exact apply time on Friday (early,
  to maximize weekend buffer)? When a batch is NOGO'd, does each item re-prompt
  automatically next Friday, or does a NOGO'd item need a fresh research refresh
  first? How long may an eligible item sit in the queue before it's force-escalated?
- **Q9 — Digest/podcast gating (D18).** Which update outputs go into the daily
  digest briefing (just decisions, or the field-report highlights too)? And the
  exact "all requirements met" set that releases the podcast — is the
  update-research step a hard gate, or best-effort with a timeout so a slow
  research run can't indefinitely block the morning podcast?
- **Q10 — Field-report sources (D15).** Confirm the job-C source set (r/OpenWebUI,
  GitHub issues, forums) and whether per-service source hints belong in the
  manifest (e.g. a service's issue tracker URL) to focus the search.

## 18. References

- Manual per-service runbook this automates: LiteLLM guide §19.3 / §19.4
  ([`../LiteLLM-Proxy/guide-LiteLLM-Proxy.md`](../LiteLLM-Proxy/guide-LiteLLM-Proxy.md))
- Research engine contract: `OB1/integrations/research-service/` +
  [`../research-engine-for-OB/`](../research-engine-for-OB/)
- Claim ingestion: `OB1/integrations/research-curator/index.ts`
  (`/ingest/research-package`), `OB1/docker/init-claims.sql`
- Gmail send pattern: `OB1/recipes/daily-digest/src/clients/`
- Watchtower today: `docker-compose.yml` (watchtower), `scripts/post-update-hook.sh`
- Future human-approval sink: [`../teams-chat-agent-orchestration/`](../teams-chat-agent-orchestration/)
- Workspace conventions (three-place rule, git etiquette): [`../../CLAUDE.md`](../../CLAUDE.md)
- OAuth expiry caveat: `daily-digest-oauth-7day-expiry` memory
