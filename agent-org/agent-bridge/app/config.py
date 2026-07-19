"""Environment-driven config (no secrets in files — PLAN §8 / README G-convention).

Everything is read from the process environment (compose injects it). Defaults are
chosen so the service *and its tests* run without a live stack: DATABASE_URL defaults
to a local SQLite file so the gate FSM can be exercised with zero external deps.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AO_", extra="ignore")

    # ── Service ────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # ── State store (fail-safe persistence — governance §3.0 invariant i) ───
    # SQLAlchemy async URL. Postgres in prod (asyncpg); SQLite default for
    # dev/tests. A `frozen` effort MUST survive a restart, so this store is the
    # source of truth for gate state — never in-memory in production.
    database_url: str = "sqlite+aiosqlite:///./agent_bridge.db"

    # ── Chat platform (Mattermost) — OD-3: behind a ChatAdapter interface ───
    chat_adapter: str = Field(
        default="fake",
        description="fake | mattermost — which ChatAdapter to instantiate",
    )
    mattermost_url: str = "http://mattermost:8065"
    mattermost_bot_token: str = ""          # env only (no secrets in files)
    mattermost_ws_url: str = ""             # derived from mattermost_url if empty
    mgmt_channel: str = "mgmt"              # Human Operator <-> PO <-> PM (§7)
    # The OPERATOR-FACING canonical URL (the tailnet serve, e.g. https://…:8446) — NOT the internal
    # mattermost_url the bridge connects to. Used ONLY to build clickable effort-thread permalinks so
    # a dispatch message links straight to the live command stream (observability = safety). Empty →
    # links degrade to plain effort ids. Mirrors MM_SITE_URL so both resolve to the same host.
    mattermost_site_url: str = ""

    # ── Comms model (COMMS-MODEL — channel = project, effort = thread) ───────
    # One stable channel per project (#proj-<slug>); efforts are THREADS inside it, so
    # the sidebar never grows with task volume. Two permanent function channels carry
    # cross-cutting operational signal (the §2 routing table).
    default_project: str = "sandbox"        # #proj-sandbox for throwaway/unassigned work
    incidents_channel: str = "incidents"    # wake-storm / undeliverable / crash notices
    suggestions_channel: str = "suggestions"  # worker suggestion pool -> learning loop (§6)
    # Notification discipline (CM.6): coalesce this many rapid worker commands into one
    # thread post to keep effort threads readable. Failures/denials always post immediately.
    activity_batch: int = 5

    # ── Inference backpressure resilience ───────────────────────────────────
    # The shared single-GPU llm-queue can shed requests (429/503) when a batch job (research /
    # ingestion) saturates it. A bridge model call retries with exponential backoff before giving
    # up, so a transient GPU squeeze degrades gracefully (the PO says "model's busy, one moment")
    # instead of surfacing as a bogus "couldn't parse". NOT a health probe (C5) — it only reacts to
    # a real request being shed.
    model_backpressure_retries: int = 3
    model_backpressure_base_delay_s: float = 1.5
    model_backpressure_max_delay_s: float = 8.0
    # Capacity park-and-resume: an orchestration step shed after the retries above is PARKED (machine
    # B suspended, reason=inference_backpressure) instead of failed, and auto-resumed when capacity
    # returns. The resume driver drains parked efforts ONE AT A TIME — driven by the self-clocked
    # capacity signal (a successful call) with a timer as the fallback tick.
    capacity_resume_enabled: bool = True
    capacity_timer_s: float = 45.0          # fallback re-check cadence when no success signal fires
    capacity_max_attempts: int = 6          # resume attempts before escalating a starved effort
    # Source guard (anti-self-DoS): the orchestration's own research/grounding call is SKIPPED if a
    # shed happened within this window — don't add a fan-out on top of a saturated GPU.
    capacity_source_guard_s: float = 60.0
    # Stall watchdog (operator 2026-07-10: "there hasn't been an update in 2 hours"): the org must
    # never sit silent after a dispatch. A tick sweeps for efforts wedged mid-dispatch (silent past
    # the threshold, not delegating, not parked) and auto-re-engages them, escalating past the cap.
    stall_watchdog_s: float = 240.0         # sweep cadence
    stall_threshold_s: float = 900.0        # silence past this (15 min) with no progress = wedged
    stall_max_recoveries: int = 2           # auto-re-engages before a loud escalation stands
    # WORKER LIVENESS — silence detection, not a wall-clock deadline (P9 register #25, 2026-07-17).
    # A worker that HANGS mid-turn holds task status `running` forever, so `has_running_task` reports
    # `busy` and the stall sweep DEFERS indefinitely — a real hang (arm D) sat 20 min, uncaught, with
    # GPU at 0%. The fix asks the daemon's per-agent-step event offset (`/tasks/{id}/events`
    # `next_offset`, which advances on generation/tool/edit — unlike the shell-only `activity` array):
    # a working worker's offset climbs every tick; a hung one's is FROZEN. If a running worker's offset
    # has not advanced for `worker_silence_s`, it is hung → cancel the turn + recover the effort. This
    # NEVER interrupts legitimate long work (a working worker keeps bumping the offset), so the value
    # can be small and generous at once. 0 disables the check (pure legacy busy-defer).
    worker_silence_s: float = 300.0         # running but offset frozen this long (5 min) = hung
    # Autonomous INFRA-freeze recovery (operator 2026-07-13: "fully autonomous would be my choice,
    # but send a message so i can see; i don't need approval"). When the PM monitor freezes an effort
    # on an ENVIRONMENT/WORKSPACE symptom (no .git, clone missing, "repository setup") rather than a
    # real code deviation, the org self-heals by re-cloning + retrying (this many times) and only
    # escalates to the human if the re-clones don't take. A real work-deviation still needs the human.
    infra_recovery_cap: int = 2
    # Cross-effort A→B DEBUG HANDOFF (operator 2026-07-14: workers "work with each other by
    # providing debug logs for errors they've run into outside of their current workspace. This
    # engages/wakes the other worker to fix the bug and push. Worker is told the bug was fixed and
    # wakes again to continue its work."). BRIDGE-MEDIATED, never peer-to-peer — the floor's
    # bus-only (#3) and escalate-up (#7) rules hold: the org routes the log, wakes the OWNING
    # project's worker as a normal gated effort, and resumes the reporter when the fix lands.
    # Depth 1 by design (a fix effort may not hand off again — that reaches the human).
    handoff_enabled: bool = True
    handoff_cap: int = 2                    # handoffs per reporting effort before escalating
    # WORKER-SIDE PLAN GATE (operator 2026-07-14: "plan mode could be used to ensure alignment to
    # the task — save wasted time working on the wrong thing, additional steering and ultimately
    # starting over"). Before touching any code, the worker plans in a READ-ONLY turn (edit/write
    # tools excluded — headless plan mode) in its OWN session; the PM checks the plan against the
    # goal (forbidden terms, delete-to-pass intent, an LLM off-goal lens). Misaligned → one
    # revision with the reason → still misaligned → honest stop BEFORE any wasted work. The
    # approved plan stays in the session, so execution continues from it.
    # `off` = never, `risky` = high-blast-radius efforts only (default), `all` = every effort.
    worker_plan_gate: str = "risky"
    # FLAIL GUARD arming (P9 Phase 0 instrument, 2026-07-16). The daemon kills a coding turn that
    # reads for 25 calls / 15 min without ever editing; the bridge then forks a FRESH session from
    # the base goal. That fork is a SUSPECT in the P8 quality regression: it is the one mechanism
    # that discards the worker's model of the code mid-effort, and P9's thesis is that quality
    # comes from the worker holding a coherent model. This field exists to MEASURE that, not to
    # fix it — turn it off for one round and let the operator judge the product. Defaults to the
    # live behaviour (armed) so the unit suite's wake expectations are unchanged.
    worker_flail_guard: bool = True
    # POST-DELIVERY QA / EXPLORATORY EVALUATION (operator 2026-07-15, reviewing gym PR#2: the
    # delivery passed its own tests and read well, but was frustrating to actually USE — no help
    # systems, and a SEPARATE little-coder QA pass surfaced a page of gaps "that could've been
    # caught with a simple QA from the original orchestration effort"). Green tests != a good
    # product. After a delivery lands + passes its check, a DIFFERENTLY-GOALED QA agent (it did
    # NOT build the thing) EXERCISES the product as a skeptical user — runs it, tries each
    # function + malformed/edge inputs, checks for usage help and clear errors — and reports
    # DEFECTS (in scope of the goal → fixable now) vs FOLLOWUPS (out of scope → operator's call).
    # The report rides on the PR and the closure. Modes:
    #   off     — no QA pass (the pre-2026-07-15 behaviour; the field DEFAULT so the unit-test
    #             harness — which counts worker wakes on delivery — is unchanged)
    #   report  — run QA, attach the findings to the PR + thread; the human decides
    #   iterate — additionally auto-iterate ONCE on the in-scope DEFECTS before the PR
    # `iterate` trades wall-time + a scope-expansion risk for a more finished product; `report`
    # keeps the human as the disposer of what's worth fixing (governance §6). The DEPLOYED bridge
    # sets `AO_QA_GATE=report` in compose so real deliveries get QA; the default stays `off`.
    qa_gate: str = "off"
    # QA CODE-REVIEW LENS (operator 2026-07-15, evaluating the delivered tool a 4th way: "evaluate
    # the code cleanliness — is it SOLID, industry-standard patterns, clear naming, does it support
    # documentation?"). The functional QA above is BLACK-BOX (run the product, feed it garbage); it
    # cannot see missing docstrings, absent type hints, a data-layer `sys.exit` that should `raise`,
    # or `sys.path` packaging hacks. When on (and qa_gate != off) a SECOND, differently-goaled
    # reviewer READS THE SOURCE for craftsmanship & documentation (governance §4.4 — a distinct
    # review role, not a second happy-path pass); its defects feed the same iterate loop. Default
    # off so the wake-counting unit harness is unchanged; the DEPLOYED bridge / the gym turn it on.
    qa_code_review: bool = False
    # DEVELOP-BRANCH INTEGRATION (operator 2026-07-15, reviewing gym PR#2-5: "the PRs should be
    # separate as they are now, but they should be MERGED INTO DEVELOPMENT" — the org left N
    # parallel PRs off main and never integrated them into one converging product "like an actual
    # project"). When on, each ACCEPTED per-effort delivery (green + its own PR) is additionally
    # MERGED into a per-project `develop` branch that accumulates the whole product, and ONE
    # standing `develop → main` PR is kept as the complete-product gate (merge to main stays
    # human — only the merge-into-develop is autonomous). The per-effort PRs stay exactly as they
    # are. Default off (unchanged behaviour); the DEPLOYED bridge / the gym turn it on.
    develop_integration: bool = False
    develop_branch: str = "develop"
    # CLOSURE INVARIANT (P8 #1, 2026-07-16 gym: two complete, green products closed "done" with
    # `delivery_pr_opened: 0` — no PR, no QA, no develop-integration — and nothing noticed until a
    # human queried the audit table; the org's REPORT and its AUDIT disagreed). When on, the PM may
    # not claim "done" on a LANDED delivery unless the effort's own audit proves the gates that
    # should have run actually did (PR opened; QA evaluated when qa_gate != off; develop
    # integration ATTEMPTED when it's on and the delivery was accepted). A missing gate refuses the
    # close: `closure_invariant_failed` is audited, an honest "could not deliver" needs-attention
    # is posted, and the effort stays open. A genuine read-only/no-changes completion has no
    # delivery, hence no gates to assert. Default off (house rule); the DEPLOYED bridge / the gym
    # turn it on via AO_CLOSURE_INVARIANT.
    closure_invariant: bool = False
    # ── P10 THE DRAIN LOOP (ORCHESTRATION-DESIGN §4, §5, §6.5) ───────────────
    # The org RAN OUT OF WORK BEFORE THE PROJECT WAS DONE: it QA'd once or twice, hit the `n >= 2`
    # auto-iterate cap, and stopped — with no task queue and no notion of "next item", so "nothing
    # left to do" was an *accident of an empty model reply* rather than a computed fact (gym-007/008:
    # defects trickled 6 → 5 → none while an operator review of a comparable product found 5 bugs +
    # 3 gaps). When on, post-delivery evaluation becomes a DRAIN LOOP: three objective standing
    # lenses observe what exists → gap analysis compares that to THIS SCOPE's goal → gaps become
    # plainly-stated tasks in a content-addressed queue → the tasks are worked → repeat. A scope is
    # complete when a full sweep propagates ZERO NEW tasks — a COUNTED quantity, never a model's
    # opinion. Requires qa_gate != off (the drain replaces that evaluation). Default off so the
    # wake-counting unit harness is unchanged; the DEPLOYED bridge / the gym turn it on.
    drain_loop: bool = False
    # RUNAWAY GUARD ONLY — not the termination condition. Termination is zero propagation; this
    # only bounds a loop that propagates new work every single round (generous, like burndown).
    drain_round_cap: int = 40
    # PLAN/IMPLEMENT SPLIT (P10.5): a worker asked to plan work it JUST PERFORMED has context bias
    # by construction — gym-008's `_auto_iterate` re-sent the entire original goal to the worker
    # that had just satisfied it, which returned an EMPTY plan and stranded the effort. When on, a
    # planner turn converts the open tasks into a plan and a FRESH implementer session executes it,
    # never having defended the prior output. Safe only because the CDCL clause set (§5–6) carries
    # the learning across that rotation.
    drain_plan_split: bool = False
    # TIER WALK (P10.6): scopes nest, complete bottom-up, and a parent's sweep that finds a SEAM
    # defect writes the task into the OWNING CHILD and flips it back to open — "complete" is a
    # current state, not a terminal one. Off leaves the drain flat (effort-scoped, no tree).
    drain_tier_walk: bool = False
    # A round that propagates at least this many tasks is evidence the scope spans several concerns
    # — split it, using the tasks themselves as the description of what those concerns are.
    # Decomposition is therefore DERIVED FROM OBSERVED WORK, not guessed at intake.
    drain_decompose_threshold: int = 5
    # Depth is a reliability tax even when tokens are free: loss compounds per hop (§4). Tier only
    # as deep as the scope genuinely nests.
    drain_max_tier_depth: int = 2
    # Autonomous burn-down round cap (operator 2026-07-07: "all 138 errors should have been worked
    # through autonomously and not elevated in the first place"). This is a RUNAWAY GUARD, not a
    # check-in: a STILL-PROGRESSING campaign should run to green, not elevate mid-progress — so the cap
    # is generous. A genuinely STUCK campaign elevates FAR sooner via the 2-consecutive-no-progress
    # stall detector; the cap only bounds a campaign that progresses every single round (a big port).
    burndown_round_cap: int = 40
    # Branch reaper (operator 2026-07-11: "when a new branch replaces the last, just delete the last
    # — it's abandoned; no human code is lost in an agent branch"). The org auto-deletes SPENT/
    # ABANDONED `agent/*` branches (merged into main, or superseded by a newer effort's branch),
    # keeping open-PR branches + the newest/in-flight effort's branch. agent/* only.
    branch_reaper_enabled: bool = True
    branch_reaper_s: float = 900.0          # reap cadence
    # A SUPERSEDED agent branch (an older one, when a newer agent branch exists on the repo) whose
    # last commit is older than this is ABANDONED → reaped and its open PR closed, so the operator
    # sees ONLY the current work (operator 2026-07-12: "3 branches again, confusing; I don't know
    # where to look"). A recent superseded branch is kept (it may be parallel live work).
    branch_stale_hours: float = 36.0

    # ── Conversation context (hierarchical + bounded — the PO's memory) ──────
    # A reply builds thread-level context; the channel is a higher-level background. Both are
    # char-budgeted so they never overwhelm the model window, and the channel layer is filtered to
    # what's RELEVANT to the current query (lexical overlap). Tune the budgets to the model's window.
    context_thread_chars: int = 2500       # immediate: the current thread's recent turns
    context_channel_chars: int = 1500      # background: relevant turns from elsewhere in the channel
    context_max_thread_turns: int = 14

    # ── Local model lane (existing air-gapped llm-gateway — PLAN §3.4) ──────
    # Callers reach inference transparently via the `llama-cpp` alias. We add
    # nothing to this gateway; we only consume it. NEVER probe model health (C5).
    local_api_base: str = "http://llama-cpp:8080/v1"
    local_api_key: str = "agent-org"       # any non-empty string (permissive gateway)
    worker_model: str = "qwen36-27b"
    judge_model: str = "qwen36-27b"        # same model = zero swap thrash (OD-10)

    # ── Cloud model lane (separate llm-gateway-cloud — CONDITIONAL, Pc) ──────
    # Only wired if the P0.5 capability-floor gate mandates a cloud judge.
    cloud_enabled: bool = False
    cloud_api_base: str = "http://llm-gateway-cloud:4000/v1"
    cloud_api_key: str = ""                # a virtual key (env only)

    # ── Worker pool (scheduler — PLAN §3.6, machine B) ──────────────────────
    # Static, conservatively-sized semaphore: there is NO live GPU-occupancy
    # signal (/slots is dead on llama-swap — C6), so the interactive reserve is
    # held by CONFIG, not by probing. Default 1 worker at 3-parallel @ ~83k.
    max_concurrent_workers: int = 1
    # Base URLs of the pooled little-coder daemons (comma-separated). Empty in
    # P0-P4; filled when the worker profile is enabled (P5).
    worker_instance_urls: str = ""
    # The ACTIVE execution-sidecar toolchain images (mirrors compose's AO_OT1_IMAGE/AO_OT2_IMAGE)
    # — lets the bridge derive env-template egress (modules/envs.py) from what the operator
    # activated: the compose var IS the clearance.
    ot1_image: str = ""
    ot2_image: str = ""
    worker_poll_interval_s: float = 3.0
    # A single worker ROUND's ceiling. Raised 1800→5400 (2026-07-13): the FNA→MonoGame port's FIRST
    # heavy composition round (audit + replace ALL FNA deps across the codebase, then commit + push the
    # vendored-murder submodule) ran ~120 real commands and was cut off at 30 min BEFORE it could push,
    # so it produced no delivery, never reached a build check, and the auto-retrying burn-down loop
    # never engaged. Heavy first rounds need room to finish + push; later error-fixing rounds are
    # smaller and finish well inside this. Still bounded, so a genuine hang is still caught.
    worker_poll_timeout_s: float = 5400.0
    # Reliability: when a dispatch to a worker fails because the daemon is wedged (409 busy) or
    # unreachable, quarantine that worker for this long (a self-healing back-off — after it lapses the
    # worker is retried, so a transient wedge recovers on its own) and re-dispatch on another worker up
    # to `worker_dispatch_max_attempts` times. Stops a stuck daemon from trapping an effort forever.
    worker_quarantine_seconds: float = 300.0
    worker_dispatch_max_attempts: int = 3
    # A single poison event that keeps throwing is dead-lettered after this many handler failures
    # (marked processed + escalated) so it can't replay forever on every catch-up.
    event_max_attempts: int = 5
    # DELIVERY-PIPELINE D1: open a GitHub PR for every verified delivery (the 'promotion artifact'
    # that makes branch work VISIBLE in GitHub's UI). Merge stays human-gated (D4).
    auto_pr: bool = True
    # FALLBACK repo only. The org works on ANY project onboarded via `/project add` (a repo per
    # project, resolved from the effort's #proj-<slug> channel — see modules/projects.py). This is
    # just the default for a #mgmt request that names no project; empty = the sandbox pool. If set,
    # it is auto-registered as a project on boot so it appears in the registry + gets a channel.
    default_repo: str = ""
    # Path the bridge writes the tinyproxy egress allowlist to (a volume the ao-git-egress proxy
    # mounts + reloads on change). Empty = don't manage the file (dev/tests).
    egress_allowlist_file: str = ""
    # Commit attribution: agents commit as `<role>@<domain>` (not the baked "little-coder") so
    # `git blame` + hand-off provenance identify WHICH agent did what (P5.4).
    agent_email_domain: str = "agent-org.local"

    # ── Wake bus reliability (event-gateway — PLAN §3.1.1) ──────────────────
    wake_undeliverable_bound_s: float = 300.0   # past this, an undelivered wake is a §3 trigger

    # ── Organizational-level constraints (governance §5) ────────────────────
    wake_storm_window_s: float = 60.0
    wake_storm_max: int = 12                # bounded auto-hand-offs per effort/window
    disagreement_max_exchanges: int = 6     # two agents disagree > N -> §3 trigger

    # ── Audit mirror to Open Brain (governance §5/§6, P6.2) ─────────────────
    openbrain_mirror_enabled: bool = False
    openbrain_url: str = "http://openbrain-gateway:8061"
    openbrain_key: str = ""                # env only

    # ── Cost-tiered supervision (governance §3, P3.7) ───────────────────────
    monitor_sample_rate: float = 0.25      # expensive-continuous LLM monitor sampling (0..1)

    # ── Stage 3 plan-approval + Stage 5 stop-gates/review (UX-FLOW, P3.9/P4) ─
    # These are the proactive alignment controls. Risk-gated by default (like the dry-run + review
    # depth) so routine one-liners stay fast; set to `all` for the strict-spec reading (every effort
    # gets a plan-approval gate + review), or `off` to skip.
    plan_approval: str = "risky"           # always | risky | off — Stage-3 plan-approval gate (P3.9)
    review_mode: str = "risky"             # all | risky | off — Stage-5 checkpoints+monitor+review (P4)

    # ── Ground + dry-run, risk-gated (UX-FLOW Stage 4, P4.0) ────────────────
    # Grounding submits an effort's assumptions to the shared openbrain-research service
    # (reach it BY CONTAINER NAME on ai-stack_llm-net; :8818 host loopback is unreachable from
    # a container). Best-effort + OFF by default (like the OB mirror) — advisory context, never a
    # gate. The risk-gated DRY-RUN is the actual execution gate (§4.0/§4.5).
    grounding_enabled: bool = False
    research_url: str = "http://openbrain-research:8000"
    research_key: str = ""                  # env only (if the service requires one)
    # RS.2 (REPO-SOURCES-WIRING §5): auto-ingest an onboarded repo's docs/manifests into Open
    # Brain as primary sources (on /project add, on a D4 merge, or NL "sync <project> docs") so
    # repo questions get claim-checked, cited answers. The ENGINE does the work; this only
    # controls the bridge's thin triggers.
    repo_sync_enabled: bool = True
    grounding_timeout_s: float = 300.0      # bound the poll; on timeout grounding is skipped
    grounding_poll_interval_s: float = 5.0

    # ── Advisory (Tier 2) — research-grounded answers to design/architecture questions ──
    # When ON, an `advisory` operator message is answered by running an openbrain-research job and
    # replying with the grounded, CITED synthesis in-thread (falls back to a clearly-labelled
    # ungrounded local-model take if research is unavailable). Reuses research_url/key above. This is
    # the operator's own private research egress (Mullvad/Tor), so it's on by default; the future
    # Tier-3 cloud lane will be an explicit per-question opt-in on the same intent.
    advisory_enabled: bool = True
    # NOT a decision gate (operator-caught: a fixed timeout abandoned a job ~35 s before it
    # finished). The advisory poll is STATE-DRIVEN — it reads the job's own status (queued/running/
    # done/error + queue_position) and decides; progress is posted to the operator as it changes.
    # This value is ONLY the runaway backstop for a job that claims to be alive for hours (engine
    # wedged). The advisory runs as a background task, so a long wait blocks nothing.
    advisory_timeout_s: float = 7200.0

    # ── GitHub App — the capability plane's root of trust (autonomous-project-lifecycle P-APL.0) ──
    # A GitHub App (NOT a long-lived PAT) authorises the governed capability plane (fork/create/
    # submodule). The DURABLE secret is the App private key (a mounted file — never in git, never in a
    # worker); it only signs a JWT to mint SHORT-LIVED, per-installation, revocable installation
    # tokens. `github_app_owner` = your personal account login the App is installed on. Disabled until
    # `github_app_id` + a readable key file are present, so nothing breaks before you register the App.
    github_app_id: str = ""                          # numeric App ID from the App's settings page
    # A read-only bind mount of agent-bridge/secrets/ (the DIRECTORY always exists via .gitkeep so
    # `up` never breaks; the .pem is gitignored + dropped in by the operator). Absent file → plane off.
    github_app_private_key_path: str = "/etc/agent-bridge/secrets/github-app-key.pem"
    github_app_owner: str = ""                       # the account (personal login) the App is installed on
    github_api_base: str = "https://api.github.com"

    @property
    def github_app_enabled(self) -> bool:
        """The capability plane is live only when the App is configured (id set + key file present).
        Everything gates on this so the bridge runs normally before the App is registered."""
        import os
        return bool(self.github_app_id) and os.path.isfile(self.github_app_private_key_path)

    # ── Project-context anchor (UX-FLOW Stage 1, P3.8) ──────────────────────
    # Before the readiness gate, run a ONE-TIME read-only worker survey of the repo (languages,
    # structure, conventions) and cache it per project, so the gate ANCHORS to the real codebase
    # instead of guessing (and stops asking placement/language/pattern questions). Only fires when
    # a real repo is focused (default_repo / per-project repo); the sandbox has nothing to survey.
    project_survey_enabled: bool = True

    # ── Charters / rule store (P3) ──────────────────────────────────────────
    charters_dir: str = "charters"
    floor_dir: str = "floor"
    profiles_dir: str = "profiles"


@lru_cache
def get_settings() -> Settings:
    return Settings()
