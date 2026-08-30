# `.githooks/` — version-controlled git hooks

`.git/hooks/` is **not** version controlled, so a hook living only there is lost on
a fresh clone and drifts silently between machines. These are the real ones.

## Activate (once per clone)

```bash
git config core.hooksPath .githooks
```

Verify:

```bash
git config --get core.hooksPath   # -> .githooks
```

## What `pre-commit` enforces

| # | Check | Script | Blocks on |
|---|-------|--------|-----------|
| 1 | **Secret guard** | [`scripts/checks/check-staged-secrets.ps1`](../scripts/checks/check-staged-secrets.ps1) | any staged env-shaped file, or a staged blob containing a recognizable provider token / private-key block |
| 2 | Line endings | `scripts/checks/validate-lineendings.ps1` | repo line-ending convention |
| 3 | Gateway-only LLM routing | `scripts/checks/check-llm-gateway-routing.ps1` | an inference/serve endpoint pointing at a `*-upstream` server instead of the LiteLLM alias |
| 4 | Project configs (staged-aware) | `scripts/checks/check-project-configs.ps1` | a staged `.yml` that fails to render, or a staged `.ps1` that fails to tokenize |
| 5 | `env_file` scope (staged-aware) | `scripts/checks/check-env-file-scope.ps1` | a commit that ADDS a service granting itself a shared `.env` |
| 6 | **Attestation** (not a check — a record) | inline, step 6 | nothing. It appends the validated tree to `<git-common-dir>/hook-attest.log` so `scripts/checks/check-hook-attestation.ps1` can prove afterwards that 1–5 ran |

Only **staged** content is scanned, so the secret guard stays fast — it never walks
the working tree or the vendored/data directories.

## The other hooks

| Hook | Does |
|---|---|
| `pre-merge-commit` | Runs the same list for a **clean** merge, whose tree `git commit` never sees. It `exec`s `pre-commit` rather than duplicating it |
| `commit-msg` | When a submodule gitlink is staged, every SHA-shaped token in the message must resolve to a real commit — a bump may not name a commit that does not exist |
| `reference-transaction` | The **commit-path guard** — see below |

## `reference-transaction` — the commit-path guard

Everything above is skippable with `--no-verify`, which leaves no trace in any git
object. That is not hypothetical: an agent reached for it on its first commit, which is
the observation `PLAN 0 A7` records as FALSIFIED ("cloud/worktree agents can be governed
normatively"). The attestation ledger made the bypass *detectable* — but only when a
reviewer looks, and only after the commit already sits on the branch.

`reference-transaction` closes it at the point content becomes history. Measured on
git 2.49 (2026-08-30):

```
git commit --no-verify   ->  pre-commit SKIPPED, commit-msg SKIPPED,
                             reference-transaction STILL RUNS
exit non-zero at `prepared`
                         ->  "fatal: ref updates aborted by hook", exit 128,
                             the branch does not move
```

So the rule is: **a `refs/heads/*` ref may not be advanced to a commit whose tree is not
in the attestation ledger.** `git commit --no-verify`, `git commit --amend --no-verify`
and `git merge --no-verify` are refused; the refusal is appended to
`<git-common-dir>/hook-guard.log`, which is the audit record U5 asks for
(`scripts/agent-harness/verify-commit-path-guard.ps1 -Audit` prints it).

Honest work is deliberately untouched — ref *moves* (branch creation, `reset`,
fast-forward, `git worktree add -b`), and anything the sequencer replays (`rebase`,
`cherry-pick`, `revert`, for which git runs no `pre-commit` at all). Sequencer skips are
logged rather than passed silently, and `check-hook-attestation.ps1` still reports them
at merge time. The 33-check drill is
[`scripts/agent-harness/verify-commit-path-guard.ps1`](../scripts/agent-harness/verify-commit-path-guard.ps1),
and it carries a negative control: with the hook removed, the same bypass succeeds.

**What it cannot stop**, measured rather than reasoned about (2026-08-30). A worktree
agent runs as the operator's own user with the real `git.exe`, so there is no privilege
boundary to hide a policy behind. Two routes survive:

- `git -c core.hooksPath=/nonexistent commit …` — turns off every hook, this one
  included. It is also the operator's deliberate escape hatch above, and the commit is
  reported **UNATTESTED** by `check-hook-attestation.ps1` afterwards.
- parking the commit under a ref **outside** `refs/heads/*` (`git update-ref
  refs/pre/x <sha>`) so the branch update reads as a ref move. The direct form is
  closed — `git update-ref refs/heads/b <unattested>` is itself refused.

Deleting the ledger is **not** a route: the guard fails closed (`reason=no-ledger`). It
is the *detector* that a deleted ledger neutralises — `check-hook-attestation.ps1`
reports INACTIVE with no ledger — so the two mechanisms fail in opposite directions.

What changed is cost and trace: `--no-verify` was one word that left no evidence at all;
each surviving route is a deliberate, unusual, separately visible command. The full
boundary is in `documentation/notes/u5proxy-findings.md`.

## Why the secret guard exists

2026-08-20: `.env.bak-pre-mtp` and `.env.bak-pre-qwen38` were committed and only
caught at **push** time by GitHub push protection, which matched **two GitHub PATs**.
Each file actually held **~25 live credentials** — Authelia JWT/session/storage keys,
the Cloudflare tunnel token, the Mullvad WireGuard private key, the Tailscale auth
key, Mattermost and Telegram bot tokens, the LiteLLM master key, DB passwords, and
`WEBUI_SECRET_KEY`.

GitHub only pattern-matches *its own* token format. **The block was luck** — the other
~25 credentials were not in any format GitHub recognizes and would have been published.
`.gitignore` covered `.env` and `.env.killswitch-*.bak`, but not `.env.bak-*`.

Two lessons baked in here:

- **Do not rely on a remote-side scanner.** It knows a handful of vendor formats and
  nothing about this stack's own keys. Catch it at commit time.
- **Defence in depth.** `.gitignore` is now broad (`.env.bak*`, `.env.*.bak`,
  `.env-bak*`, `*.env.bak*`), *and* the hook blocks env-shaped filenames outright,
  so a gap in one still gets caught by the other.

## False positives

The content patterns are deliberately narrow (specific provider token formats, not a
generic `KEY=<long string>` rule) because this repo's docs discuss credentials by name
constantly — noisy hooks just train people to bypass them.

If you hit a genuine false positive, **`git commit --no-verify` no longer works** — the
commit-path guard refuses to advance the branch to a tree the checks never validated, and
that is the whole point of it. In order of preference:

1. **Fix the check.** A pattern that fires on a documentation example is a defect in the
   pattern, and fixing it is cheaper than the next two options for everyone after you.
2. Rephrase the example so it is not a live-credential shape.
3. As a deliberate, visible operator action, turn every hook off for the one command:

   ```bash
   git -c core.hooksPath=/nonexistent commit -m "..."
   ```

   The resulting commit will be reported as **UNATTESTED** by
   `scripts/checks/check-hook-attestation.ps1` at merge time. Say so in the submission —
   that report is the record, and an unexplained one reads exactly like a bypass.

Never use any of these to push a real credential through. If a real credential was
staged, treat it as **compromised and rotate it** — unstaging is not enough, because it
may already exist in a local object or a reflog entry.
