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
| 5 | env_file scope | `scripts/checks/check-env-file-scope.ps1` | any `env_file:` in a staged compose file whose target resolves to the repo root `.env`, to a directory that is neither the compose file's own nor a parent of it, or outside the repo — whether or not HEAD already carried it (the pre-existing-grants exemption was removed on 2026-09-19 with the last two grants it covered; there is no per-service exemption). **Policy: an `env_file` value must be a PLAIN PATH LITERAL — a `*alias`, an `&anchor`, a `!tag`, a `>`/`|` block scalar and a `${VAR}` are REFUSED by policy with the token printed, never resolved** — the check reads the SHAPES compose accepts (scalar, flow sequence, block sequence, long-form `path:`) but interprets no YAML SEMANTICS, because three attempts at doing so each shipped a silent hole. What reaches the resolver is an allowlist, `^[A-Za-z0-9_./\\~-]+$` after quotes and comments are stripped; anything else is refused and printed. A merge key (`<<: *tpl`) needs no special handling: the `x-` block's own `env_file:` line is scanned where it is written. `-All` audits the whole tree |
| 5b | OB1 recipe tests | `scripts/checks/check-ob1-recipe-tests.ps1` | a staged OB1 gitlink bump whose tree fails any `*.test.mjs` under `OB1/recipes` — or whose tree the gate cannot honestly prove (staged≠disk SHA, dirty tracked OB1 files, missing node, zero tests, or fewer test **files or cases** than the pin it replaces; the case count is what catches a revert that removes a fix together with its own catching test) |
| 5c | OB1 Deno recipe type-check | `scripts/checks/check-ob1-deno-recipes.ps1` | a staged OB1 gitlink bump whose `OB1/recipes/daily-digest` does not `deno check` — the Deno recipe 5b's `*.test.mjs` glob cannot see. Type check only, not the test suite; skips instantly with no gitlink staged; **refuses** rather than skipping when `deno` is missing |
| 5d | OB1 integration images | `scripts/checks/check-ob1-integration-images.ps1` | a staged OB1 gitlink bump whose pin diff touches an `integrations/<svc>/` that carries a Dockerfile, when a relative import reachable from the entrypoint is not COPYed into the image (static, seconds, no docker) or the image does not `docker build` / `deno check` (built under a throwaway `ob1-gate/<svc>:<sha7>` tag, `--network none`, removed after). Green = "builds and resolves", not "the service works"; skips instantly with no gitlink or no image-bearing change; **refuses** when Docker is unreachable |
| 6 | Attestation | (inline in the hook) | nothing — records the checked tree **and the hash of the hook file that checked it** in `<git-common-dir>/hook-attest.log`, so `--no-verify` leaves an absence that `check-hook-attestation.ps1` can read back, and a passing gate can be traced to the hook that ran it (see [Which hook gated this tree?](#which-hook-gated-this-tree)) |

## Which hook gated this tree?

Each attestation line is `<tree> <utc-timestamp> <branch> <hook-blob-hash>`, appended by
step 6. The fourth column is `git hash-object "$0"` — the hook file git was *executing*,
not a path the hook computed. That distinction is the whole feature: `core.hooksPath` is an
**absolute** path to one checkout, so a worktree's own edited `.githooks/pre-commit` is not
what runs for that worktree's commits, and any derived path would record the hook we wish
had run.

Ask which hook gated a commit:

```bash
LEDGER="$(git rev-parse --git-common-dir)/hook-attest.log"
grep "^$(git rev-parse '<rev>^{tree}')" "$LEDGER"
```

Ask whether that hook contained a given check — e.g. 5b, the OB1 recipe-test gate:

```bash
git cat-file -p <hook-blob-hash> | grep -c 'check-ob1-recipe-tests'
```

`1` (or more) means the gate really was in the hook that ran; `0` means the commit was
validated, but not by that check. A `git cat-file` that fails means the gating hook was an
**uncommitted local edit** — also an answer, and a more interesting one. A `?` in the
fourth column means the hash lookup itself failed; the attestation still stands, the hook
identity is simply unknown. Three-column lines predate 2026-09-04.

This exists because "the hooks ran" and "the check you are relying on ran" came apart:
item `wiki-mirror-hardening` produced three honest attestations from a genuinely executing
hook that did not yet contain check 5b, and establishing that afterwards cost a reviewer an
hour of reflog archaeology. It answers going forward only — the ledger cannot speak for
commits made before the column existed, and does not pretend to.

`check-hook-attestation.ps1` prints the distinct gating hooks for the commits it checks.
It **reports** them; the pass/fail verdict is still attested-vs-not and nothing else.

A fourth column is not always a hash. **The checker's own output is where the four states
are named and told apart** — a hash, the `?` sentinel, an absent column, and anything else
(quoted verbatim as `MALFORMED`, which is what the four 2026-08-30 lines from the reverted
commit-msg attester read as, their fourth column being a branch name) — and it is also
where a merge line explains why it carries `pre-commit`'s hash rather than
`pre-merge-commit`'s. That is deliberate: those are answers a reader needs *at* the line
they are staring at, not in a reference they would have to know to go and find. This
paragraph is a pointer to it, not a second copy of it.

A clean `git merge` never runs `pre-commit`, so `pre-merge-commit` delegates to it —
the merge commit's tree passes the same eight checks (and a gitlink merge therefore
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
