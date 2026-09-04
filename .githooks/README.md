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
| 3b | Corpus exposure plane | `scripts/checks/check-corpus-exposure-producers.ps1` | a recognized direct corpus INSERT that does not state its exposure plane (best-effort text scan; the DB's NOT NULL + CHECK is the real enforcement — read the check's own output for what it cannot see) |
| 4 | Project configs | `scripts/checks/check-project-configs.ps1` | a staged compose file that does not render, or a staged .ps1 that does not tokenize |
| 5 | env_file scope | `scripts/checks/check-env-file-scope.ps1` | a commit ADDING a service that grants itself a shared .env (pre-existing grants are reported, not blocked) |
| 5b | OB1 recipe tests | `scripts/checks/check-ob1-recipe-tests.ps1` | a staged OB1 gitlink bump whose tree fails any `*.test.mjs` under `OB1/recipes` — or whose tree the gate cannot honestly prove (staged≠disk SHA, dirty tracked OB1 files, missing node, zero tests, or fewer tests than the pin it replaces) |
| 6 | Attestation | (inline in the hook) | nothing — records the checked tree in `<git-common-dir>/hook-attest.log` so `--no-verify` leaves an absence that `check-hook-attestation.ps1` can read back |

A clean `git merge` never runs `pre-commit`, so `pre-merge-commit` delegates to it —
the merge commit's tree passes the same seven checks (and a gitlink merge therefore
needs the OB1 working tree moved to the incoming pin *before* merging).

Only **staged** content is scanned, so the secret guard stays fast — it never walks
the working tree or the vendored/data directories.

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

If you hit a genuine false positive:

```bash
git commit --no-verify
```

Use it for a documentation example, never to push a real credential through. If a real
credential was staged, treat it as **compromised and rotate it** — unstaging is not
enough, because it may already exist in a local object or a reflog entry.
