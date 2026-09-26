# `.githooks/` — version-controlled git hooks

`.git/hooks/` is **not** version controlled, so a hook living only there is lost on
a fresh clone and drifts silently between machines. These are the real ones.

## Activate (once per clone)

```bash
git config core.hooksPath .githooks
```

Verify, by EXECUTING a hook (a listing is not enough, see below):

```bash
git config --get core.hooksPath                               # -> .githooks
./.githooks/commit-msg /dev/null && echo "hooks can run"      # must print: hooks can run
```

(`commit-msg` exits 0 at once when it is given no message file, so this runs the hook and
checks nothing else.)

**If that does not print `hooks can run`, NO gate runs on your commits.** The same goes for
any `hint: The '.githooks/pre-commit' hook was ignored because it's not set as executable.`
line from `git commit`. git skips a hook it cannot execute, runs no check, and the commit
succeeds. Two causes:

* **The files lost their `x` bit.** The hooks are committed as mode `100755`, so a normal
  `git clone` gets it right. A copy made some other way (for example, through a filesystem
  that keeps no modes) needs it back:

  ```bash
  chmod +x .githooks/pre-commit .githooks/commit-msg .githooks/pre-merge-commit
  ```

* **The checkout is on a `noexec` mount.** There `ls -l` still shows `-rwxr-xr-x` (and
  some shells' `test -x` still says yes), but nothing on the mount can execute, and `chmod`
  does not help.
  The verify line above fails with `Permission denied`. Clone somewhere that allows execution.

`pre-commit` also refuses to commit a tree in which a file named as a git hook
(`pre-commit`, `commit-msg`, ...) has lost `100755` in the index
(`git update-index --chmod=+x <file>` fixes it, on Windows too). That catches the regression
where it would be committed. The hook cannot report its own missing bit, and nothing in this
repository can detect a `noexec` mount for you. Both are why the verify step executes a hook.

## Which host runs the gates

Every gate is a PowerShell script, and they run on Linux and macOS too. At the top of
`pre-commit` the hook picks a host, first match wins:

| Mode | When | What runs |
|------|------|-----------|
| **Windows PowerShell** | a native Windows git shell (`uname -s` is `MINGW*`/`MSYS*`/`CYGWIN*`, e.g. Git Bash) and `powershell.exe` is on `PATH`. **Not WSL**: WSL has `powershell.exe` on `PATH` through interop, but its Windows git cannot use the Linux checkout, so WSL takes the next row | every gate, with the same command line the hook has always used |
| **PowerShell 7** | not a native Windows git shell (or no `powershell.exe`), and `pwsh` is on `PATH` (any OS) | every gate, the same `.ps1` files |
| **Python only** | neither, `python3` (3.8+) is on `PATH` | the three gates that are never skipped, through their Python twins; every other gate prints `SKIPPED <gate>: needs PowerShell (install pwsh to run it)` and does not fail the commit |

The three never-skipped gates and their twins, both under `scripts/checks/`:

| Gate | PowerShell | Python twin |
|------|-----------|-------------|
| secret guard | `check-staged-secrets.ps1` | `check_staged_secrets.py` |
| line endings | `validate-lineendings.ps1` | `validate_lineendings.py` |
| gateway routing | `check-llm-gateway-routing.ps1` | `check_llm_gateway_routing.py` |

Each twin copies its `.ps1`'s rules and says in its header what it copies. **All three FAIL CLOSED, in both
languages:** if the gate's own `git` call fails (a wrong `GIT_DIR`, a repository git refuses
as "dubious ownership"), or routing cannot list a directory or read a candidate file, the gate
refuses the commit and names the failure. A query that failed is never read as "nothing
staged". **Change one
and you change both.** With no PowerShell and no Python 3 at all, the hook refuses the
commit at the secret guard (`REFUSED secrets: ...`) rather than skip it.

A skip is never silent. The hook prints each `SKIPPED` line as it happens, ends with a
summary such as

```text
Pre-commit gates (host: python3) - RAN: hook-modes secrets line-endings gateway-routing | SKIPPED: doc-placement corpus-exposure project-configs env-file-scope ob1-recipe-tests ob1-deno-recipes ob1-integration-images
```

and adds a fifth column to the attestation line (below), `skipped=<gate>,<gate>,...`.
Install `pwsh` to run every gate. `commit-msg` needs no PowerShell in any mode: it is
POSIX `sh` with `git`, `grep`, `awk` and `sort`. `pre-merge-commit` runs `pre-commit`,
so it picks its host the same way.

**One gate can skip itself on a PowerShell host** (ac-corpus-gate, 2026-09-26): gate 3b,
corpus exposure, when the clone was made without `--recurse-submodules`. Every producer it
recognises lives in the OB1 submodule, so with `OB1/` empty its scan is vacuous. When the
index records the OB1 gitlink and `OB1/.git` is absent (a plain clone, or after
`git submodule deinit`), and the rest of the tree is clean, the gate prints
`SKIPPED - the OB1 submodule is not initialised ... Run: git submodule update --init OB1 (CI checks it).`
and exits 78; the hook records it SKIPPED (its `run_gate` line passes a sixth argument,
`yes`, and no other gate does). A violation outside OB1 still fails the commit, a checkout
of OB1 at any commit is scanned exactly as before, and CI checks OB1 out and reads 78 as a failure.

## What `pre-commit` enforces

| # | Check | Script | Blocks on |
|---|-------|--------|-----------|
| 1 | **Secret guard** | [`scripts/checks/check-staged-secrets.ps1`](../scripts/checks/check-staged-secrets.ps1) | any staged env-shaped file, or a staged blob containing a recognizable provider token / private-key block |
| 2 | Line endings | `scripts/checks/validate-lineendings.ps1` | repo line-ending convention |
| 3 | Gateway-only LLM routing | `scripts/checks/check-llm-gateway-routing.ps1` | an inference/serve endpoint pointing at a `*-upstream` server instead of the LiteLLM alias |
| 3b | Corpus exposure plane | `scripts/checks/check-corpus-exposure-producers.ps1` | a recognized direct corpus INSERT that does not state its exposure plane (best-effort text scan; the DB's NOT NULL + CHECK is the real enforcement — read the check's own output for what it cannot see) |
| 4 | Project configs | `scripts/checks/check-project-configs.ps1` | a staged compose file that does not render, or a staged .ps1 that does not tokenize |
| 4b | Generated doc blocks | `python scripts/stack/stack.py docs --check` (inline in the hook; Python 3.11+ on every host, found independently of the PowerShell host) | a generated block in a registered doc (`stack.py docs --list`) that no longer matches the manifest and the compose renders, or a registered block whose markers are missing or broken - named by file and block. Runs only when a compose file, an `.env.example`, the manifest, `stack.py`, the OB1 gitlink or a registered doc is staged; once triggered, refuses while any of those inputs (bar the OB1 gitlink) carries UNSTAGED edits, staged or not, because the check reads the working tree. Exit 3 (a block or row that cannot be rendered here - OB1 absent or not at the STAGED gitlink / carrying tracked edits, no docker) is recorded as SKIPPED `docs-blocks`, never as a pass, and says those were NOT compared; a plane-table is still compared row by row. No Python 3.11 is SKIPPED too. **On a MERGE commit the step always runs, and REFUSES a stale block it could compare or an OB1 that is checked out but not at the merged gitlink / dirty (`docs --check` exit 4); a machine without docker, an OB1 checkout or Python 3.11 gets a WARNING and a SKIPPED gate, not a refusal** (a newcomer's `git pull` must not be blocked) - merge into a line with `--no-ff` (a fast-forward runs no hook; see MERGE-PROTOCOL.md step 5). **CI's `stack-driver` job is the gate that never skips**: it checks out OB1 and runs `docs --check` with docker and without `--allow-unverified` |
| 5 | env_file scope | `scripts/checks/check-env-file-scope.ps1` | any `env_file:` in a staged compose file whose target resolves to the repo root `.env`, to a directory that is neither the compose file's own nor a parent of it, or outside the repo — whether or not HEAD already carried it (the pre-existing-grants exemption was removed on 2026-09-19 with the last two grants it covered; there is no per-service exemption). **Policy:** an `env_file` value must be a PLAIN PATH LITERAL; the check refuses YAML indirection instead of resolving it, printing `env_file values must be plain path literals; YAML anchors and aliases are refused by policy (rewrite as the path)`. To find the value, it reads EVERY line indented deeper than the `env_file:` key (with one carve-out: a line at EXACTLY the key's indent whose body starts with `-` is part of the value, because YAML lets a block sequence sit at its parent key's own indent — a bare SCALAR there is not the value and stays unflagged) and that a trailing comment on the key (`env_file:  # note`) neither ends the value nor replaces it — the block below is still read — the value's extent is decided by indentation before any shape is read, and a blank or comment-only line does not end it — and reads each of those lines in the four shapes compose accepts (scalar, flow sequence, sequence item, long-form `path:`), including a scalar on the line AFTER the key and an item written under a bare `-`; a line inside the extent that it does not recognise is treated as a VALUE, never as the end of the block. What reaches the resolver is an allowlist, `^[A-Za-z0-9_./\\~-]+$` after quotes and comments are stripped; anything else is refused and printed. A merge key (`<<: *tpl`) needs no special handling: the `x-` block's own `env_file:` line is scanned where it is written. `-All` audits the whole tree |
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

A fifth column, `skipped=<gate>,<gate>,...`, appears only when the hook ran in Python-only
mode (see [Which host runs the gates](#which-host-runs-the-gates)) and names the gates it
did not run. Without it such a line would read exactly like one from a host that ran every
gate. `check-hook-attestation.ps1` reads columns 1 and 4 only, so the fifth column changes
no verdict; it is there for a human reading the ledger.

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
