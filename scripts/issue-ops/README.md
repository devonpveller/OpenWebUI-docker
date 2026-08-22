# issue-ops — the Part M issue pipeline (operator console + machinery)

GitHub issues → audited plans → Mattermost-governed execution, per
CLEANUP-PLAN Part M (M.1–M.7). Roles: **local org drives** (agent-org
workers), **Claude gates** (independent PR review), **operator merges**.

## For a Claude session answering the operator in Mattermost

When the operator asks about issues ("current issues", "what's planned",
"execute #24"), this is the console — always run with the ops venv:

```powershell
D:\...\ai-stack\.venv\Scripts\python.exe scripts\issue-ops\issue_ops.py status
```

| They say | You run | Then |
|---|---|---|
| "current issues" | `status` | post the rendered view to the thread |
| "plan issue N" / plan is 🔴 or stale | `plan N [--refresh]` | summarize the written plan; plans are cheap and always allowed |
| "execute N" | `radar N` first | STALE plan ⇒ refuse + `plan N --refresh` (M.3). `verdict` ≠ `fix` or `repro` ≠ `confirmed-in-code` ⇒ refuse — the plan's `## Disposition` holds the draft reply; get operator approval in the thread before posting ANYTHING public to GitHub. Overlaps ⇒ ask for the operator override (M.6). Focus lock set ⇒ it queues — say so. Then follow the plan in an ISOLATED worktree off `origin/development`, never the operator's checkout; `touches_live: true` steps need per-action approval in the thread (M.4) |
| a worker PR appears | `gate <PR#>` | post the verdict (RECOMMEND-MERGE / DENY + orchestration-adjustment plan) to the thread; the operator merges, never you |
| live validation (M.8 T2) | `t2 N <plane> <service> --probe "<cmd>" [--image tag]` | ephemeral `test-<service>` twin on the LIVE networks (fresh volumes, no host ports, no prod aliases — twins must NEVER reuse a prod service name: shared-network DNS round-robins); evidence lands in `state/t2-issue-N-evidence.txt`; teardown is automatic. Probes that write through host BIND mounts touch live surfaces — M.4 approval first |
| "pause issues" / big arc starting | `focus set "<arc>"` | `focus clear` when they release it |

## Files

- `issue_ops.py` — the CLI (status/plan/radar/gate/focus/seed)
- `github_app_auth.py` — installation tokens from the agent-org GitHub App
  (key at `agent-org/agent-bridge/secrets/github-app-key.pem`; no gh CLI)
- `config.json` (optional) — overrides: `repo`, `target_branch`,
  `stale_after_commits`
- `state/` (gitignored) — token cache, focus lock, known-issues registry
  (GitHub's list index lags App-created issues by minutes; the registry +
  direct fetches keep the console truthful)

**Credential scope (resolved 2026-08-22):** the App now holds **Issues:
read & write** (operator granted on the App AND accepted on the
installation — GitHub needs both steps; until the installation accepts,
comment/close 403s "Resource not accessible by integration" even though
*opening* issues on a public repo slips through). If a future permission is
ever missing, ask the operator for the App-side grant — do NOT repurpose
`LC_DEPLOY_TOKEN` or other PATs for issue writes.
- Plans live in `documentation/issue-plans/` (see its README for the
  frontmatter contract)

House rules the pipeline enforces by construction: evidence before merge,
`main` untouched, per-issue branches off `development`, maintenance-window
awareness (weekly compaction Sun 03:15, disk-guard kill-switch) before any
live-service action.
