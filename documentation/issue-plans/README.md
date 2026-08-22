# issue-plans — the Part M plan store

One file per GitHub issue (`issue-<N>.md`), written by the planner
(`scripts/issue-ops/issue_ops.py plan <N>`) or a Claude session, consumed by
the Mattermost console and the executing sessions.

Frontmatter contract (machine-read by issue-ops):

| Field | Meaning |
|---|---|
| `issue`, `title`, `created` | identity + planning timestamp (UTC ISO) |
| `base_sha`, `target_branch` | the REMOTE tip the plan was audited against — staleness is measured here, never against a local checkout (Part M.6) |
| `status` | `planned` → `approved` → `executing` → `done` (plus `queued-behind-focus`, shown live by the status view while the focus lock is set) |
| `triage` | `simple` \| `bounded` \| `heavy` — heavy issues queue for the big-model era instead of burning local-org rounds (M.7) |
| `touches_live` | `true` ⇒ every container stop/build/redeploy in execution needs per-action operator approval in the MM thread (M.4) |
| `touched_paths` | comma-separated repo paths — feeds the overlap radar (`issue_ops.py radar <N>`) |

House rules baked into every plan: validation with failing→passing evidence
before merge; work on `issue/<N>-<slug>` branches cut from `development` in an
ISOLATED worktree/clone (never the operator's checkout); `main` untouched.

A STALE plan (code moved past `base_sha`, or the issue edited after planning)
refuses execution until re-audited: `issue_ops.py plan <N> --refresh`.
