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
| 6 | ~~Attestation~~ — **moved to `commit-msg`** | — | nothing. Attesting here, in the FIRST hook, was a hole: a tree stayed attested after `commit-msg` refused the message, so the `--no-verify` retry was waved through. See [`attest-lib.sh`](attest-lib.sh) |

Only **staged** content is scanned, so the secret guard stays fast — it never walks
the working tree or the vendored/data directories.

## The other hooks

| Hook | Does |
|---|---|
| `pre-merge-commit` | Runs the same list for a **clean** merge, whose tree `git commit` never sees. It `exec`s `pre-commit` rather than duplicating it |
| `commit-msg` | Two jobs. (1) When a submodule gitlink is staged, every SHA-shaped token in the message must resolve to a real commit — a bump may not name a commit that does not exist. (2) **It is the attester**: as its last act it appends `<tree> <message-digest>` to `<git-common-dir>/hook-attest.log`. Being LAST is the point — nothing after it can veto, so an abort anywhere in the chain leaves nothing attested |
| `attest-lib.sh` | Not a hook. The one definition of the (tree, message) attestation, shared by the attester and the guard so they cannot drift apart |
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

So the rule is: **a `refs/heads/*` ref may not be advanced to a commit whose (tree,
message) pair is not in the attestation ledger.** The refusal is appended to
`<git-common-dir>/hook-guard.log` (`scripts/agent-harness/verify-commit-path-guard.ps1
-Audit` prints it).

### The message is half the pair, and leaving it out was a real hole

Attestation used to record the **tree only**, and it was written by `pre-commit` — the
**first** hook in the chain. `commit-msg` runs after it, so a rejected message left its
tree attested and the ledger entry survived the abort. Reproduced by execution
(2026-08-30, git 2.49.0.windows.1) against the hooks as they were shipped:

```
git commit -m "OB1 -> deadbee1, pushed before this bump"
    -> pre-commit passes and ATTESTS; commit-msg refuses the bogus SHA; HEAD unmoved
git commit --no-verify -m "<the identical rejected message>"
    -> LANDS. exit 0. No line in hook-guard.log. Unstopped AND unaudited.

git commit --amend --no-verify -m "<anything at all>"
    -> LANDS. The tree was unchanged and already attested, so the message could be
       rewritten to claim any gitlink SHA.
```

The fix is ordering, not more checks: the attester moved to `commit-msg`, the last hook
that can veto, and it records the **message digest** alongside the tree. Both shapes are
now refused with a `hook-guard.log` line (`reason=message-not-attested` for the amend),
and both are drill steps 11 and 11.5. `--no-verify` skips `pre-commit` and `commit-msg`
together, so there is no way to reach the attester without passing everything in front of
it.

Honest work is deliberately untouched — ref *moves* (branch creation, `reset`,
fast-forward, `git worktree add -b`), partial commits (`git commit -- path`, where git
hands the hooks a temporary index), clean and conflicted merges, and anything the
sequencer replays (`rebase`, `cherry-pick`, `revert`, for which git runs no `pre-commit`
at all). Sequencer skips are logged rather than passed silently, and
`check-hook-attestation.ps1` still reports them at merge time. The
[**commit-path drill runs 66 checks**](../scripts/agent-harness/verify-commit-path-guard.ps1),
carries a negative control — with the hook removed, the same bypass succeeds — and
executes every row of the table below.

### What it cannot stop

A worktree agent runs as the operator's own user with the real `git.exe`, so there is no
privilege boundary to hide a policy behind. This guard **closes the cheap route and
prices the rest**; it is not a containment wall. An earlier version of this page said
`git commit --no-verify` "no longer works" and that `--amend --no-verify` was "refused"
full stop, and enumerated two survivors. A reviewer measured three more. Every row below
is now executed by step 12 of the drill, including the two columns that matter most:
whether a guard-log line is written, and whether the commit is still **UNATTESTED** —
which is what `scripts/checks/check-hook-attestation.ps1` reads, and `queue.ps1 -Submit`
runs it.

| # | Route | Lands | `hook-guard.log` | Still caught at the submit gate |
|---|-------|-------|------------------|----------------------------------|
| R1 | `git -c core.hooksPath=/nonexistent commit …` | yes | no line | yes — UNATTESTED |
| R2 | `git update-ref refs/pre/x <sha>` then `git update-ref refs/heads/b <sha>` | yes | no line | yes — UNATTESTED |
| R3 | `rm .githooks/reference-transaction` then `git commit --no-verify` | yes | no line | yes — UNATTESTED |
| R4 | committing in a worktree whose **branch** does not carry `.githooks/reference-transaction` | yes | no line | yes — UNATTESTED |
| R5 | `git rev-parse HEAD > "$(git rev-parse --git-dir)/CHERRY_PICK_HEAD"` then `git commit --no-verify` | yes | `SKIP-SEQUENCER` | yes — UNATTESTED |
| R6 | `git commit --amend --no-verify --no-edit` | yes | no line | n/a — tree **and** message unchanged, so the attestation covers it |

Notes that the table cannot carry:

- **R1** is also the operator's deliberate escape hatch, documented at the top of this page.
- **R2**: parking under a non-`refs/heads` ref makes the commit "already reachable", so the
  branch update reads as a ref move. Narrowing the reachability exemption would push this to
  `git tag` and start refusing an honest `git checkout -b rel <tag>`, so it is kept wide on
  purpose. The **direct** form is closed: `git update-ref refs/heads/b <unattested>` is
  refused.
- **R3 and R4 are the same root cause**: `core.hooksPath` is `.githooks`, a **relative** path
  resolved against each worktree's own top level, so the guard is a tracked file that the
  guarded party can delete — and every branch cut before this hook merged is in state R4
  until it merges the line. Making `core.hooksPath` absolute would fix both and was NOT done:
  it would point every worktree at one branch's copy of the hooks, which breaks changing a
  hook on a branch and breaks a fresh clone. **This residue is open**, and the compensating
  control is that both leave the commit UNATTESTED.
- **R5** forges the sequencer state the guard exempts. The exemption is not optional — git
  runs no `pre-commit` for replayed commits, so a rebase can never be attested and demanding
  it would refuse every reviewer rebase in `MERGE-PROTOCOL.md`. A hook that can only read
  `.git` cannot tell a forged `CHERRY_PICK_HEAD` from a real one. **This residue is open.**
- **R6** is bounded and the bound is load-bearing: the same shape on an **unattested** commit
  is refused, so it cannot launder a commit that arrived via R1/R3/R5. It can re-parent and
  re-date; it cannot change what the commit says or holds.

Deleting the ledger is **not** a route: the guard fails closed (`reason=no-ledger`). It
is the *detector* that a deleted ledger neutralises — `check-hook-attestation.ps1`
reports INACTIVE with no ledger — so the two mechanisms fail in opposite directions.

What changed is cost and trace: `--no-verify` was one word that left no evidence at all,
and it is now closed on every shape that alters content or message. R1–R5 all still
surface at the submit gate as UNATTESTED commits; what they escape is the guard **log**,
not the audit. The full boundary, with the commands that produce it, is in
`documentation/notes/u5proxy-findings.md`.

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
