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
| "execute N" | `radar N` first | STALE plan ⇒ refuse + `plan N --refresh` (M.3). Overlaps ⇒ ask for the operator override (M.6). Focus lock set ⇒ it queues — say so. Then follow the plan in an ISOLATED worktree off `origin/development`, never the operator's checkout; `touches_live: true` steps need per-action approval in the thread (M.4) |
| a worker PR appears | `gate <PR#>` | post the verdict (RECOMMEND-MERGE / DENY + orchestration-adjustment plan) to the thread; the operator merges, never you |
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

**KNOWN LIMIT (2026-08-22):** the App installation holds `contents` /
`pull_requests` / `administration` write + `metadata` read — **no `issues`
permission**. On the public repo it can *open* issues and *read* them, but
comment/close returns 403 "Resource not accessible by integration" (and reads
would break if the repo ever goes private). Until the operator grants
**Issues: read & write** on the App and approves it on the installation,
sessions must ask the operator to close issues, or record resolution in the
plan file only. Do NOT repurpose `LC_DEPLOY_TOKEN` or other PATs for this.
- Plans live in `documentation/issue-plans/` (see its README for the
  frontmatter contract)

House rules the pipeline enforces by construction: evidence before merge,
`main` untouched, per-issue branches off `development`, maintenance-window
awareness (weekly compaction Sun 03:15, disk-guard kill-switch) before any
live-service action.
