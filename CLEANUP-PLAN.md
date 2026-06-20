# ai-stack workspace cleanup plan

A phased plan to clean and modularize the workspace ahead of the next round of
big updates. **Phase 0 is already executed** (this session); Phases 1–3 are
proposed and need your sign-off on target structure before execution, because
they touch hard-coded paths in `docker-compose*.yml`, the recovery scripts, and
`OB1/`. Follow the CLAUDE.md rule: *adding/removing/moving a container or its
files = change the compose file, the recovery scripts, and the stack-map doc
together.*

Discipline: **archive (git mv), don't delete**, anything with history or links;
explain every removal in git terms; verify a file is dead before moving it.

---

## Phase 0 — quick-wins (DONE this session)

OWUI plugin centralization (`owui/`), knowledge-migration tool generalization
(`tools/owui-knowledge-to-openbrain/`), and:

- Removed `.mcp.json.new` (byte-identical stale twin of the gitignored `.mcp.json`).
- Untracked runtime state: `logs/tailscale-monitor.pid`, `tailscale-state/tailscaled.log*.txt`
  (kept `tailscaled.log.conf`); added gitignore coverage.
- Relocated loose root files: `test_keyword_analysis.py`, `test_request.json` → `test/`;
  `emergency_recovery_access_guide.py` → `modules/emergency-recovery/`.
- Tidied `.gitignore` (dropped duplicate `logs/` + `/backups`, the garbage
  `.../__pycache__/` line, and 5 per-dir `__pycache__` entries → one `__pycache__/`).

---

## Phase 1 — remaining cruft + ambiguous files (LOW risk, needs a decision each)

Each of these had a live reference or an unresolved variant, so it was *not*
auto-removed:

| Item | Finding | Proposed action |
|------|---------|-----------------|
| `test_pipe_lmstudio.py` (root) | **differs** from `test/test_pipe_lmstudio.py` | diff the two; keep the newer in `test/`, delete the root copy |
| `tools/migration_tool.py`, `tools/refactor_orchestrator.py`, `tools/scaffold_generator.py`, `tools/validation_tool.py`, `modules/migration_report.json` | a **code-migration toolkit** (`migration_report.json` is read by the first two). Looks like one-off tooling for the `modules/` refactor. | confirm it's no longer run; if dead, move the set to `documentation/archive/` or delete; if live, give it its own `tools/module-migration/` folder |
| `modules/custom-tools/service/tailscale_serve_admin.py` vs `…_v2.py` | `_v2` is the "future HTTP API version" referenced by `scripts/lmstudio_fix_v2.py`; the non-v2 is the current OWUI one | rename for intent (`…_owui.py` / `…_http.py`) instead of a `_v2` suffix; or retire whichever is truly unused |
| `dockerfile.tailscale` | inconsistent casing vs `Dockerfile.openwebui-gpu`; referenced by `docker-compose.yml:111`, `README.md` (×3), `.github/copilot-instructions.md` (×5), `.dockerignore`, 2 docs | coordinated rename → `Dockerfile.tailscale` updating **all** references in one commit |
| `scripts/` (56 tracked) | mixes recovery, backup-install, one-off fixes (`lmstudio_fix_v2.py`, `check-*`) | sort into `scripts/recovery/`, `scripts/backup/`, `scripts/checks/`, `scripts/oneoff/` |

---

## Phase 2 — documentation restructure (MEDIUM effort, LOW risk)

`documentation/` has 117 tracked files: a flat pile of ~20 one-off notes at the
top level + 82 in `implementation-guide/` mixing shipped / never-built / active.

**2a. Split the flat top-level notes**
- → `documentation/archive/` (completed point-in-time "fix done" notes):
  `JSON_ISSUE_FIXED.md`, `TIMESTAMP_FIX_APPLIED.md`, `PIPE_IMPLEMENTATION_COMPLETE.md`,
  `ROUTING_FIX_OCTOBER_2025.md`, `PIPE_RECOVERY_SYSTEM_TEST_RESULTS.md`,
  `TAILSCALE_SERVE_EXECUTION_GUIDE.md`
- → `documentation/runbooks/` (live operational guides):
  `incident-response.md`, `monitoring-access.md`, `backup-conventions.md`,
  `restore-from-snapshot.md`, `AUTONOMOUS-RECOVERY-GUIDE.md`, `PREVENTION-GUIDE.md`,
  `UPDATE-MANAGEMENT.md`, `TAILSCALE_SERVE_QUICK_REFERENCE.md`, `LM_STUDIO_TAILSCALE_SETUP.md`

**2b. Split `implementation-guide/`** into `shipped/` vs `proposed/`:
- shipped (live): `LiteLLM-Proxy`, `update-owui-to-0-9-6`
- proposed / never-built (design docs): `teams-chat-agent-orchestration`,
  `autonomous-updates-with-security`, `research-engine-for-OB`,
  `ai-stack-user-created-automations`
- (verify each against reality before sorting — some are partially built.)

**2c. Conventions**
- Adopt one filename convention (kebab-case); rename the SHOUTY_SNAKE notes.
- Add `documentation/README.md` as an index (what's a runbook vs an archived note
  vs an active plan).
- Retire/refresh `.github/copilot-instructions.md` and root `UPDATE-QUICK-START.md`
  — both still describe the pre-refactor pipe structure / the finished 0.9.6 upgrade.

---

## Phase 3 — architecture: separation of concerns (HIGHER risk, big payoff)

**3a. Consolidate the scattered status-pipe subsystem.** One logical component is
spread across four top-level dirs:
`scripts/ai_pipes/` (orchestrator + legacy pipes) + `core/router.py` +
`modules/` (capability modules) + `schemas/` (module manifests). Collapse into a
single cohesive component, e.g.:

```
ai-stack-pipe/
  orchestrator.py        (was scripts/ai_pipes/unified_openwebui_pipe.py)
  router.py              (was core/router.py)
  modules/               (was top-level modules/)
  schemas/               (was top-level schemas/)
  pipes-legacy/          (the superseded individual *_pipe.py, or delete)
```
Touches: the orchestrator's `ROUTER_SCRIPT_PATH`/module paths, the compose mount
for `/host_project`, `test/`, and the many doc references to
`unified_openwebui_pipe.py` / `core/router.py` / `modules/`. The deploy snapshot
stays `owui/pipes/server_status.py`.

**3b. Clarify the top-level taxonomy.** Today the root mixes services, the pipe
subsystem, ops, config, and docs at one level. Target:

```
services/      little-coder, smolcrawl, llm-queue, mnemory-gateway,
               openbrain-gateway, search-gateway   (each a deployable)
owui/          deploy-by-paste plugins              (done ✓)
ai-stack-pipe/ the consolidated subsystem (3a)
ops/           scripts/, backup/, recovery
config/        (already exists)
deploy/        docker-compose*.yml, Dockerfile.*, entrypoint.sh
documentation/
```

**Decision needed:** whether to move the service folders under `services/`
(cleanest, but rewrites compose build contexts + every recovery-script path +
the stack-map doc + possibly `OB1/`'s external-network assumptions), or leave
services at root and only do 3a + the docs/cruft phases. **Recommended:** do
Phases 1–2 and 3a first; treat 3b (service grouping) as a separate, deliberate
migration with the stack-map skill driving the 3-places update.

---

## Suggested order

1. Phase 1 file decisions (fast, removes ambiguity).
2. Phase 2 docs restructure (no code risk).
3. Phase 3a subsystem consolidation (contained, high clarity payoff).
4. Phase 3b service grouping — only if desired, as its own migration.
