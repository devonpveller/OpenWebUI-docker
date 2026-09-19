# Findings — U2 git-issue intake door (2026-08-30)

## DECISIONS entries to append

### 2026-08-30 · U2 · class 2 — the cadence owner is NOT supercronic
DECISION: The daily sweep and weekly synthesis are scheduled by the HOST
          Scheduled Task family (`scripts/issue-ops/register-issue-cadence.ps1`),
          not by supercronic.
CITED:    §C.3 decision 4 names supercronic (OB1's crontab) as the cadence owner,
          and §C.3 itself says "if implementation shows a default wrong, that is
          a class-2 decision: pick the better option, log it with the evidence".
EVIDENCE: `issue_ops.py` shells to a headless `claude` binary (`_claude_bin`),
          reads the GitHub App private key from
          `agent-org/agent-bridge/secrets/github-app-key.pem`, and runs `git`
          against the repo root. None exists inside an OB1 container, and every
          entry in `OB1/docker/cron/crontab` is an HTTP call to a service on
          obnet. Containerising the planner is a larger piece of work than the
          cadence it would carry.
REVERT:   `register-issue-cadence.ps1 -Unregister`. Nothing else depends on the
          tasks existing; the commands remain runnable by hand.

### 2026-08-30 · U2 · class 2 — registration is left to the operator
DECISION: `register-issue-cadence.ps1` ships the mechanism and does NOT register
          itself. `-WhatIfOnly` shows exactly what it would create.
CITED:    §C.2 class 4 — "spending real money or calling external services beyond
          the session". Registering starts an unattended daily job that runs
          `claude -p` per unplanned or stale issue.
REVERT:   n/a (nothing was registered).

---

## F1 — `--post` is refused, not half-built

The weekly synthesis prints its verdict thread and exits 3 on `--post`. The
Mattermost console (M.2) already owns that channel, and posting from here would
make a second writer to it. A stated refusal with a non-zero exit beats a silent
no-op that looks wired.

**To close:** route the body through the M.2 console rather than adding an
independent poster.

## F2 — the sweep cannot run from a worktree

`issue_ops.py` needs `agent-org/agent-bridge/secrets/github-app-key.pem`, which is
gitignored and — unlike the `.env` files added to `worktree.env_files` — is a
private key that should NOT be copied into every worktree.

Verified: `sweep --dry-run` fails in a worktree with "GitHub App not configured",
and succeeds from the main checkout (3 open issues, all 3 selected).

**Not a defect to fix by copying the key.** Recorded so the next person does not
conclude the sweep is broken. If worktree execution is ever needed, the answer is
a read-only mount of the key directory, the shape used for the anchor schema.

## F3 — a real overlap exists right now

The first live synthesis run flagged **#24 ↔ #29**, which both touch
`OB1/recipes/daily-digest/link-enrich.ts` (#29 was split from #27). That is a
genuine collision the operator should see before either is approved, and it means
§2's "a deliberately overlapping issue pair must be flagged" is satisfied against
production data as well as a fixture.
