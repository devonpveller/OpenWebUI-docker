# CLAUDE.md — ai-stack workspace

Self-hosted AI stack: Open WebUI + local llama.cpp inference behind a LiteLLM gateway
with an admission queue, a memory layer (Open Brain), a private search
gateway, a coding agent with a governed multi-agent org, and a gated internet portal.
Each plane is its own compose project around a root `docker-compose.yml` that declares
only the shared `ai-stack_*` networks. [`README.md`](README.md) is the map; this file is
the rules.

## Driving the stack

- **[`stack.manifest.toml`](stack.manifest.toml) is the inventory of record** (planes
  and products). **The driver is the front door:** `python scripts/stack/stack.py <verb>`
  ([`scripts/stack/README.md`](scripts/stack/README.md); `stack.ps1` is a shim). Per-host
  enablement lives in the gitignored `.stack/state.json`; the driver never writes the
  manifest. To turn something on: `stack.py enable <plane|product>`, then `up`
  (`enable` merges into the state file; `init --force` replaces it).
- **Each plane's README is the detailed one:** [frontend](frontend/README.md) ·
  [inference](inference/README.md) · [search](search/README.md)
  · [coder](coder/README.md) · [portal](portal/README.md). Topology: the `/stack-map` skill
  or [its reference](.claude/skills/stack-map/references/workspace-stacks.md).
- **Every plane owns its `.env`** (from `<plane>/.env.example`) and its own
  `COMPOSE_PROFILES`. A variable lives in the file of the plane whose service reads
  it; never pass `--env-file`.
- Bring Open Brain up only after `llm-gateway` is healthy; tear it down before the
  planes it depends on. The portal is started only by `scripts/portal/portal-on.ps1`.
- Recovery after a crash or netns break: `scripts/recovery/emergency-recovery.ps1`.

## Rules that protect the live stack

- **Never route inference around LiteLLM.** Callers use `http://llama-cpp:8080` /
  `http://llama-cpp-embed:8080` (aliases on `llm-gateway`); only health/GPU/recovery
  probes may target `*-upstream` directly (`scripts/checks/check-llm-gateway-routing.ps1`
  enforces it). Every new inference consumer needs its own LiteLLM virtual key.
- **Never GET LiteLLM `/health` through the alias** (it loads every model); probe
  `/health/liveliness`. Other gotchas: [inference/README.md](inference/README.md).
- **Never restart `openwebui` alone** — `tailscale` shares its network namespace;
  order is openwebui, wait healthy, then tailscale.
- **Posture: private and local-first, cloud-capable, every cloud part shipped inert.**
  What reaches the internet and how to re-derive it:
  [README.md, "Posture"](README.md#posture-local-first-cloud-capable). Never give
  `llm-gateway` an egress path as a side effect of anything.
- **Container rule:** adding/removing/moving a container = the plane compose file +
  `stack.manifest.toml` + recovery (`emergency-recovery.ps1` + `stack.ps1`) + the
  stack-map reference doc, together; the full checklist (backups, watchdog, health
  probe, inventory) is [SERVICE-LIFECYCLE.md](documentation/runbooks/SERVICE-LIFECYCLE.md).
- **Verify against gitignored evidence** before declaring anything dead: `.env*`
  values and `backup/models/` OWUI exports are where "zero references" verdicts
  die (`grep --no-ignore`, the live `webui.db`).
- **Archive, don't delete:** retired code goes to `scripts/archive/` (see its README
  provenance table); retired docs go to `../documentation-plans-ai-stack/journal/archive/`.
- **Secrets** live only in `.env` files and `secrets/` (gitignored); never stage one.

## Git and parallel work

- **Never commit or push on the user's behalf unless explicitly asked.** Hooks:
  `git config core.hooksPath .githooks`; never `--no-verify`. What each hook runs:
  [.githooks/README.md](.githooks/README.md).
- **Branches:** `main` is untouched (the known-good deliverable, promoted only by the
  operator); `development` is the live-hosted line; work happens on branches cut from
  `development` and merges back only with validation + testing evidence.
- **Worktree-per-session.** Never several sessions committing in one checkout; the main
  checkout is the operator's (reading there is fine). At your first *mutating* intent
  (stage, commit, branch, gitlink bump) run `scripts/agent-harness/new-worktree.ps1 -Id
  <short-id>` and work in the path it prints — never bare `git worktree add` or
  `EnterWorktree name:`.
- **Agree the goal first, then hand off:** `queue.ps1 -Propose -Anchor <json>` (the
  operator confirms), then a test plan and `queue.ps1 -Submit`. **You do not test or
  merge your own work.** Pipeline:
  [MERGE-PROTOCOL.md](documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md);
  tooling: [scripts/agent-harness/README.md](scripts/agent-harness/README.md) and
  [MODULE.md](scripts/agent-harness/MODULE.md) (configuration, off switch).
- **Plan work keeps a tracker:** a session working through a plan keeps a per-card commentable tracker artifact (reuse the plan's, else [plan-tracker](.claude/skills/plan-tracker/SKILL.md)), updated on every state change; comments are context, never authorization.
- **Testing:** hold the plane's lease (`lease.ps1 -Acquire -Name <plane>`) before a
  test that mutates a plane or needs it stable. Test images tag `:wt-<id>`; prod
  containers and `:local` tags are a gated deploy, not a test; never attach test
  containers to the `ai-stack_*` networks.
- **Never rebuild a `:local` image as a side effect** of other work: a rebuild is a
  deliberate deploy of that image, done on its own.
- **OB1 is a pinned submodule** (clone with `--recurse-submodules`, or `git submodule
  update --init`). Push OB1 changes to OB1's remote FIRST, then bump the gitlink in a
  commit saying what moved; never bump it to a commit not on that remote. OB1 runs the
  `openbrain-gateway:local` image: `docker build -t openbrain-gateway:local ./openbrain-gateway`.

## Where documentation goes — the plan store

This CODE repo is public-surface; plans, notes, findings, evidence and test plans name
internal hosts, ports and file:line anchors, so they go to the private plan store
[`documentation-plans-ai-stack`](https://github.com/devonpveller/documentation-plans-ai-stack.git), cloned beside this one as `../documentation-plans-ai-stack`:

| What | Where (in the plan store) |
|---|---|
| a plan, build log, task list, `NN-*.md` plan set | `implementation-guide/<feature>/` |
| a harness anchor | `implementation-guide/<feature>/anchors/<id>.json` |
| a work item's findings (its `findings_sink`) | `implementation-guide/<feature>/findings/<id>.md` |
| a work item's test plan | `implementation-guide/<feature>/test-plans/<id>.md` |
| a note or finding with no feature | `journal/notes/<topic>-<yyyy-mm-dd>.md` |
| evidence: logs, transcripts, measured output | `journal/evidence/<id>/` |
| a retired doc or closed plan | `journal/archive/` |
| EXCEPTION: live agent-org / little-coder subproject docs (all of `agent-org/docs/`, incl. its `log/`; `little-coder/`'s docs) | stay in THIS repo, beside their code; a retired one follows the archive rule like any doc |

1. Run `scripts/checks/plan-store.ps1` at the start and before you stop; fix what it lists.
2. Commit **and push** the store in the same sitting (`git pull --rebase` first); a new
   feature also gets one status row in [the index](documentation/implementation-guide/README.md).
3. **Findings never go into the deliverable** — write them to the findings file, with
   what was checked and when.
4. A plan set that landed here untracked: `scripts/checks/plan-store.ps1 -Migrate <feature>`.

**This repo keeps** only what someone needs with just this checkout: `CLAUDE.md`,
`README.md`, `SECURITY.md`, `documentation/runbooks/`, the status index and
`multi-agent-concurrency/`, per-plane and per-module READMEs, the agent-org / little-coder
subproject docs (live ones stay here), and evidence that CODE reads (beside
that code). Enforced by `scripts/checks/check-doc-placement.ps1` (its Python twin
`check_doc_placement.py` where there is no PowerShell); a deliberate exception
is `AI_STACK_PLAN_IN_CODE_REPO=1` with the reason in the commit message.

## Conventions

- **Shell:** Windows + PowerShell 5.1 (ASCII no-BOM for scripts it parses);
  recovery scripts assume Docker Desktop.
- **Lint:** `ruff check .` (F + E9 gate; subprojects carry their own configs).
- **Use subagents** for a review briefed to refute, a broad sweep, or parallel work (one
  message, backgrounded). Name the claim and what would disprove it; ask only for what it
  verified. Check the part of its report you act on before relaying it. A subagent you
  spawned is not an independent party for the harness's separation of duties.
- **Delegate by tier:** see `scripts/agent-harness/harness.config.json` `model_tiers` (cloud and local maps; `queue.ps1` prints the pick).

## Pointers

- Product menu, quickstart, posture, health, backups, repo map → [`README.md`](README.md);
  security → [`SECURITY.md`](SECURITY.md)
- Runbooks (updates, backups, incidents, env migration) → [`documentation/runbooks/`](documentation/runbooks/)
  and [`documentation/sysadmin-out-of-band-channel.md`](documentation/sysadmin-out-of-band-channel.md)
- Server Status pipe → [`frontend/status-pipe/`](frontend/status-pipe/README.md); OWUI
  plugins (deploy-by-paste, `manifest.csv`) → `frontend/owui/`; search gateway →
  [`search/gateway/README.md`](search/gateway/README.md)
- Plans, the operator journal and this file's retired history → `../documentation-plans-ai-stack/`
