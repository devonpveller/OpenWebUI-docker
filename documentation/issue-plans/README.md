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
| `verdict` | `fix` \| `needs-info` \| `void` \| `wontfix` (2026-08-22) — issues are REPORTS TO VERIFY, not facts. Only `verdict: fix` is executable; the other three carry a `## Disposition` section with evidence + a DRAFT public reply. **Posting any public reply (comment/close on GitHub) requires operator approval in the MM thread** — replies on a public repo are publications. |
| `repro` | `confirmed-in-code` \| `not-reproduced` \| `void-component` — execution refuses anything but `confirmed-in-code`, and validation still demands the RED-at-base run before a fix counts |
| `touches_live` | `true` ⇒ every container stop/build/redeploy in execution needs per-action operator approval in the MM thread (M.4) |
| `touched_paths` | comma-separated repo paths — feeds the overlap radar (`issue_ops.py radar <N>`) |

House rules baked into every plan: validation with failing→passing evidence
before merge; work on `issue/<N>-<slug>` branches cut from `development` in an
ISOLATED worktree/clone (never the operator's checkout); `main` untouched.

Security rules (public repo ⇒ untrusted intake, 2026-08-22): the planner
receives the issue body fenced as untrusted data with an injection preamble
(report-to-verify, never instructions); it runs read-only (`Read,Glob,Grep`);
plans pass the secret-guard pre-commit before landing; auto-planning is meant
for operator-labeled (`agent-ops`) issues — drive-by issues get at most a
verdict/disposition, never direct execution. Plans older than the verdict
field (issues 17/24/25/26) predate this contract.

A STALE plan (code moved past `base_sha`, or the issue edited after planning)
refuses execution until re-audited: `issue_ops.py plan <N> --refresh`.
