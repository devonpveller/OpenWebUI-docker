# scripts/archive/

Retired operational code, kept for history and provenance. Nothing in this
directory is referenced by any live path — compose files, scheduled tasks,
hooks, recovery scripts, or modules. Each subdirectory notes what retired it.

| Set | Retired | Why / replaced by |
|---|---|---|
| `lmstudio/` | 2026-08-20 | LM Studio fully retired as an inference target. The 0.11.0 upgrade removed both OWUI connections (`update-owui-to-0-11-0/UPGRADE-PLAN.md`); all inference runs through the LiteLLM gateway → llama.cpp. The four `169.254.83.107` scripts had been dead since 2025; `lmstudio_fix_v2.py` lost its last caller with the retirement. |
| `legacy-pipes/` | 2026-08-20 | The Sept-2025 per-capability OWUI pipes + template, superseded by the unified status pipe (`scripts/ai_pipes/unified_openwebui_pipe.py` → deployed as `owui/pipes/server_status.py`, `frontend/owui/` since 2026-09-25). Zero code references at retirement. |
| `legacy-tests/` | 2026-08-20 | The old `test/` directory: 4 of 6 files had `sys.path` pointing at directories that never existed, one tested the retired ollama container; the root-level functional twin targeted the retired LM Studio path. Replaced by pytest smoke tests in the status-pipe consolidation. |
| `templates/` | 2026-08-20 | CLI/library scaffolding boilerplate from the pipe era; docs-only references. |
| `migration-toolkit/` | 2026-08-20 | One-shot 2025-09 module-migration tooling (`migration_tool`, `refactor_orchestrator`, `scaffold_generator`, `validation_tool` + its report). The migration it performed is long done; the help/custom-tools modules stopped advertising it in the same commit. |
| `owui-knowledge-to-openbrain/` | 2026-08-20 | Its purpose ended when OWUI Knowledge was retired (commit `9223516`, 2026-08-20): the SenseGlove promotion ran in June, the rest of the Knowledge layer was dropped in favour of OB1. |
| `dfu-done/` | 2026-09-25 | `dfu-done.ps1` (the dark-factory-unification board), `verify-dfu-done.ps1` (its self-contained drill) and `redprove-fixture-cleanup.ps1` (a red-proof that drives the board), from `scripts/checks/`. The board's input is the DFU plan set (`PLAN.md`, `DECISIONS.md`, `WALKTHROUGH.md`, both ledgers) and `documentation/notes/`; adoption-closeout `ac-journal-move` moved both to the private plan store (`../documentation-plans-ai-stack/implementation-guide/dark-factory-unification/`, `journal/notes/`). A committed fixture copy would have been an audit of a frozen plan nobody edits, and left pointing at the store it could only exit 1 in CI, which has no store - so the board and its CI job (`dfu-done` in `.github/workflows/ci.yml`) were retired together. To run it by hand against the store: `-PlanPath`, `-DecisionsPath`, `-WalkthroughPath`, `-NotesDir`, `-ManualResults`, `-Dispositions`; clause 2 reconstructs the plan's history with `git log` in `-RepoRoot` and will not find it there since the move. |
| `misc/` | 2026-08-20 | `environment_config.py` (zero references workspace-wide), `tailscale_serve_admin_v2.py` (the *v1* is the live one — dispatched by `tailscale_serve_pipe.py`; v2's only caller was the retired LM Studio fixer), `backfill-syntheses.sh` (one-shot 2026-06 data migration), `test_router.py` (ad-hoc scratch test). |
| `tailscale-state/` | 2026-09-25 | One file, `tailscaled.log.conf`, moved from the repo root by ac-planes-contained. It is a tailscaled logtail config; the frontend's tailscale node state lives in `data/tailscale/` (the bind `frontend/docker-compose.yml` uses), not here. The only tracked references were two `.gitignore` lines for its rotated logs, removed with the move. No compose file, script or container mounts it (`docker inspect` of every container, 2026-09-25: the only `tailscale-state` mount on the host is another project's named volume). |

Convention: archive (`git mv`) — don't delete — anything with history; explain
the retirement in the commit message and in this table.

- `docker-compose.override.yml` — archived 2026-08-21 (Part K.5). Watchtower-era override that re-declared tailscale restart/labels/depends_on/healthcheck on the root project; every setting already lives in `frontend/docker-compose.yml` (and watchtower itself was retired 2026-08-20). With openwebui/tailscale moved to the frontend project it declared a phantom service, so it left the root.
