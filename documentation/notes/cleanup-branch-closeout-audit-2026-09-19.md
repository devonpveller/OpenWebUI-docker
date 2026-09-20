# refactor/ai-stack-cleanup close-out audit (2026-09-19)

> **Two of the `[agent]` figures below were DISPROVED when `sl-closeout` acted on
> them: the pre-commit hook runs 10 checks, not 8 (finding 7), and the
> agent-bridge suite has 865 test functions, not 866 (finding 13) — see
> `documentation/notes/stack-layers-sl-closeout-findings.md` §1 and §5 for how
> each was recounted. The note is kept as the dated record of what the audit
> said; the sink is what is true.**

Scope: what must be true before `refactor/ai-stack-cleanup` merges into
`development`, and what the branch leaves open against `CLEANUP-PLAN.md`.
Method: two refute-briefed subagents (docs vs repo; plan ledger vs repo) plus
hand verification of every item marked [verified]. Items marked [agent] carry
the agent's file:line and were not independently re-read.

## Branch state [verified]

- 706 commits ahead of `development`, 14 behind. Merge-base 2026-08-22.
- `development` has since gained: memory plane README (265fe29), PRs #25
  queue-ETA notifier, #27 digest lane, #28/#17 smolcrawl KB-name fix, #30,
  #33, and a CI `.env` stub (207d1ed).
- Files touched on both sides: `.github/workflows/ci.yml`, `CLEANUP-PLAN.md`,
  `OB1` gitlink (branch 5005197, development 3b4e502, base 48a84ae),
  `documentation/implementation-guide/LiteLLM-Proxy/J1-VIRTUAL-KEYS-CUTOVER.md`
  (moved to the plan store on this branch).
- SECURITY.md:24-33 conditions the accepted `gw-` key posture on the remote
  staying PRIVATE. A "anyone can clone" goal changes that condition.

## Docs vs repo — findings

Blocks a newcomer:
1. `.env.example:241-243` and `config/litellm.config.yaml:3,17` say the
   gateway is PERMISSIVE with no master key; `litellm.config.yaml:83` flipped
   master_key ON 2026-08-21. Leaving `LITELLM_MASTER_KEY` blank = 401 everywhere. [verified]
2. `.env.example:227-228` says set `LM_MODELS_BACKUP_CRON=` to disable; nothing
   reads that var. The real switch is `LM_MODELS_BACKUP_INTERVAL`
   (`inference/docker-compose.yml:383,420`). [verified]
3. `.env.example:105` points at `documentation/little-coder/`, which does not
   exist (moved to the plan store). [agent]
4. `README.md:147` and `CLAUDE.md:234` name `mnemory-cloud-gateway/` as a
   source dir; the dir is `mnemory-gateway/` (`memory/docker-compose.yml:68-70`). [verified by ls]

Misleading:
5. `README.md:95` restore command omits two mandatory params
   (`-SnapshotRoot`, `-Date`; `restore-from-snapshot.ps1:30-41`). [verified]
6. `README.md:107` "mirrored nightly to the NAS" — the task is weekly
   (`install-nas-backup-task.ps1:89`). [agent]
7. `README.md:45` / `scripts/README.md:37-38` list 3 pre-commit checks; the
   hook runs 8 (`.githooks/pre-commit:9-17`). [agent]
8. `README.md:27`, `agent-org/README.md:126` cite `emergency-recovery.bat`;
   archived 2026-08-21 (CLAUDE.md:25 says so). [agent]
9. `CLAUDE.md:16` "root file includes compose/<plane>.yml" — no `compose/`
   dir, no `include:`. [verified]
10. `README.md:69` "12 functional probes" — stack.ps1 has 15 Probe lines. [agent]
11. `CLAUDE.md:20` "7 coder volumes" — six (`coder/docker-compose.yml:200-207`);
    the compose comment at :201 does the wrong arithmetic. [agent]
12. `portal/docker-compose.yml:10` "networks/volumes are defined in the root
    file" — portal defines its own at :588-611. [agent]
13. `agent-org/README.md:34,121` "55 tests" — 866 `def test_`. [agent]
14. `scripts/README.md:29-32` watchdog covers "all three compose projects" —
    stack-services.json has eight. [agent]
15. `README.md:118-123` backup-interval table: `LITTLE_CODER_`, `MNEMORY_`,
    `OPENWEBUI_BACKUP_INTERVAL` are absent from `.env.example`. [agent]
16. `.env.example` `BACKUP_CRON`, `OPENWEBUI_BACKUP_CRON`, `LC_BACKUP_CRON`,
    `LITELLM_BACKUP_CRON` are read by nothing (those sidecars are sleep-loop). [agent]
17. `documentation/implementation-guide/README.md:12-13` still frames Phase 2
    as undecided; it ran 2026-09-18. [agent]

Cosmetic: OB1 "~29 containers" vs 24 services; `stack.ps1 stats` undocumented.

Missing entirely: `frontend/README.md`, `inference/README.md`,
`portal/README.md` (memory's landed on `development` in 265fe29). [verified]

Held up: per-plane service counts, host ports, all five `${VAR:?}` guards
have `.env.example` lines, every other repo-map path exists. [agent]

## CLEANUP-PLAN.md vs repo

Closed: Part A (token literal gone from tracked files, whole-repo mount gone,
Watchtower gone, hooks + gitleaks CI), B, C (superseded by the plan store),
D.1 via K, D.4, G.1/G.2, H.1, I.2/I.3, J.1/J.2/J.3, K.1-K.6, M.1-M.8 core.

Open after merge:
- Part L entirely: L.1 colocation 0/5 moves (plane dirs contain only a
  compose file — [verified]); L.2 per-plane .env 0 planes. This IS the
  operator's current ask.
- D.1 x-anchors: zero YAML anchors; `security_opt` blocks repeated 34x across
  the six plane files [verified count].
- E.1 twin gateways (openbrain-gateway/app.py 272 L vs mnemory-gateway/app.py
  267 L) — "queued next" 2026-08-22, never started. [agent]
- H.2 little-coder submodule — not done; coupled to the L.1 move. [agent]
- D-12 stack-services.json generator — hand-maintained + verified, no
  generator. [agent]
- K.6 OPEN: ~14 pre-split Docker volumes still on disk; `smolcrawl-data`
  orphan still declared in root compose. [agent]
- F.1/F.2/F.5 monoliths grew (orchestrator.py 13,801 L; bridge.py 2,397 L). [agent]
- I.4/J.6 conventions page + port registry: do not exist. [agent]
- CI gap: agent-bridge and little-coder suites not in ci.yml; routing check
  is pre-commit only. [agent]
- The plan's own ledgers stop 2026-08-22; 706 commits since are unrecorded.

## Standalone-OWUI blockers [verified]

- `frontend/docker-compose.yml:297-303` reserves an NVIDIA device — compose
  refuses to start openwebui on a host without the nvidia runtime.
- `openwebui:local` / `tailscale:local` are built from root Dockerfiles with
  context `..`; nothing pulls a stock image.
- `tailscale` is a hard companion (netns) with no authkey path documented;
  a newcomer's container sits unauthenticated.
- `inference/docker-compose.yml:34,394` bind `C:\Users\yamao\.lmstudio\models`
  literally (D.4 missed it; the agent's D.4 grep only looked for `D:/`).
- `config/` mixes portal (9 entries) with inference (4) — L.1 "config rides
  along" not started.
