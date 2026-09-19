# sl-colo-gateways - findings

Sink for harness item `sl-colo-gateways` (stack-layers PLAN.md section 2.7, wave 1).
Worker: `wt-sl-colo-gateways`, branch `work/sl-colo-gateways`, base `development` b28cbc5.
Written 2026-09-19. Everything below was checked by running the command shown, in the
worktree, on that date - nothing here is inferred from a doc.

The item itself is two `git mv`s plus pointer repoints. These are the things the move
turned up that are NOT part of it.

---

## 1. The anchor's first acceptance check names a file that has never existed

The anchor says:

```
git log --follow --oneline -- search/gateway/app.py | wc -l
```

There is no `app.py` anywhere in the search gateway tree, before or after the move. The
search gateway is a package (`gateway/src/gateway/`) whose entry module is `main.py`; the
`app.py` shape belongs to the *memory* gateway, and the anchor's "the same for
`memory/mnemory-gateway/app.py`" half is correct.

The criterion's intent - history survives the rename - is testable; the path is not. The
test plan substitutes `search/gateway/gateway/src/gateway/main.py` and
`search/gateway/searxng/settings.yml`, and keeps `memory/mnemory-gateway/app.py` as
written.

## 2. The anchor's pytest command fails, and failed identically before the move

The anchor says `python -m pytest search/gateway -q` should pass. It does not:

```
13 failed, 24 passed, 2 warnings in 11.66s
```

This is **not** a regression. The gateway's pytest configuration
(`asyncio_mode = "auto"` and `addopts = "-m 'not integration'"`) lives in
`search/gateway/gateway/pyproject.toml`. When pytest is invoked from the repo root with a
path argument it resolves rootdir/inifile upward from that argument and finds no config
(there is no `pyproject.toml`, `setup.cfg` or `tox.ini` at the repo root - `git ls-tree
b28cbc5 --name-only` confirms), so `asyncio_mode` is never set and every async test errors.

Proved by falsification against the pre-move tree, 2026-09-19:

```
git archive b28cbc5 search-gateway | tar -x -C <scratch>/premove
cd <scratch>/premove && python -m pytest search-gateway -q
-> 13 failed, 24 passed, 2 warnings in 11.69s
```

Same counts, same tests, on `development`'s own layout. The command in the anchor has
never worked from the repo root.

What does work, and what CI runs:

```
pip install -e "./search/gateway/gateway[dev]"
pytest -q search/gateway/gateway/tests      -> 36 passed, 1 deselected
```

Not fixed here: making the root-invoked form work means either a root `pytest.ini` /
`pyproject.toml` or a `conftest.py` / `pytest.ini` inside `search/gateway/`, both of which
change test configuration. That is a decision for `sl-manifest` / `sl-driver-parity`, not
for a file move.

## 3. `ruff check .` is RED on `development`, for a reason unrelated to this item

```
E501 Line too long (103 > 100)
  --> llm-queue\src\llm_queue\__init__.py:9:101
Found 1 error.
```

`llm-queue/` carries its own `pyproject.toml` whose ruff config sets `line-length = 100`
and a wider rule selection than the root `ruff.toml` (which is `line-length = 120`,
`select = ["F", "E9"]`), so the root's relaxed gate does not apply to that subtree.

The offending line is the module docstring's `Design:` pointer. `git log` puts it at
**cfa7d4e** ("documentation: move the plans to the plan store, keep only what a check
reads", 2026-09-18) - the plan-store move lengthened the path from
`documentation/implementation-guide/...` to
`../documentation-plans-ai-stack/implementation-guide/...` and pushed the line three
characters over the limit.

Consequence: the `ruff` job in `.github/workflows/ci.yml` is red on this line for every
item merged onto `development` until that one line is wrapped. `llm-queue/` is untouched by
this item (`git diff b28cbc5 --stat -- llm-queue/` is empty), and the moved trees are clean
(`ruff check search/gateway memory/mnemory-gateway openbrain-gateway` -> "All checks
passed!").

## 4. The `search/gateway/gateway/` double-nesting is what the anchor asked for

`search-gateway/` contained `gateway/` (the Python project) and `searxng/` (the
bind-mounted SearXNG config), so the literal `git mv search-gateway search/gateway` named
in the anchor and in the work instruction produces `search/gateway/gateway/src/gateway/` -
four levels spelling "gateway" - and a build context of `./gateway/gateway`.

The move was done literally, because the anchor is the contract. The alternative, for
whoever owns `sl-readmes` or a follow-up: `search-gateway/gateway` -> `search/gateway` and
`search-gateway/searxng` -> `search/searxng`, which reads as the plane's two trees (the
app, and the engine's config) rather than one nested inside the other, and would also make
the anchor's own `pytest search/gateway` path name the Python project. It costs a second
rename of the same files and was not in scope to decide here.

## 5. The memory plane cannot be built from a worktree (pre-existing)

`memory/docker-compose.yml` builds `mnemory` from `context: ../../mnemory` - the sibling
`mnemory` checkout next to `ai-stack/`, outside this repository. Rendered from a worktree
it resolves to `D:\Open WebUI\ai-stack\.claude\worktrees\mnemory`, which does not exist:

```
docker compose -f memory/docker-compose.yml --env-file .env.example config --format json
-> build mnemory -> D:\Open WebUI\ai-stack\.claude\worktrees\mnemory
```

`config` renders it happily; only a `build` would fail. The service is pinned
`image: mnemory:local` + `pull_policy: never` precisely so nobody builds it by accident,
and `memory/README.md` already documents the out-of-repo context. Unchanged by this item,
recorded because a tester rendering the memory plane in a worktree will see a
non-existent path and should know it is not ours.

Same render, same category: `mnemory-backup` binds `../backups/mnemory`, which does not
exist in a fresh worktree either (`backups/` is gitignored artifact output; compose would
create the directory on `up`). Both are pre-existing and out of scope.

## 6. `README.md` named a source tree that never existed

The repo-map row read:

```
| `llm-queue/`, `search-gateway/`, `mnemory-cloud-gateway/`, `openbrain-gateway/`, ... | Service source trees |
```

`mnemory-cloud-gateway` is the **container** name; the directory has always been
`mnemory-gateway/`. Repointed to `memory/mnemory-gateway/` as part of this move, which
fixes the wrong name as a side effect. Flagged because it is the kind of error
`sl-closeout` is counting.

## 7. Four old-path hits left standing in `documentation/evidence/research-trust/`

That file is a completed test plan for a merged item, and two classes of line live in it.

Repointed (live procedure - a reader would run these today; the item is "merged, awaiting
deploy" per the implementation-guide index):

- line 801 - "Per `search-gateway/README.md`'s own rule for an image bump"
- line 815 - the rollback step: `git checkout` the previous
  `search-gateway/searxng/settings.yml`

Left alone deliberately (rewriting them would make them WRONG):

- line 25, line 369 - `git show work/research-trust:search-gateway/searxng/settings.yml`.
  That branch still holds the blob at the old path; the command is correct as written and
  would break if repointed.
- line 64 - a `cd` into `wt-research-trust/search-gateway/gateway`, a worktree that no
  longer exists. Dead at either spelling.
- line 462 - "the parent diff touches only `documentation/`, `search-gateway/`, ..." - an
  assertion about a diff taken in September, true as written.

The anchor's grep criterion allows residue only under `scripts/archive/`,
`documentation/archive/`, `documentation/notes/` and `CLEANUP-PLAN.md`. These four are the
one class outside that list; the test plan enumerates them so the grep case is decidable.

## 8. `search/README.md` still describes an `env_file` that was deleted in 2026-08

Line ~160: "`gateway` additionally inherits the whole root `.env` through
`env_file: ../.env`". `search/docker-compose.yml` carries a fifteen-line comment at the
`gateway` service saying the opposite - the `env_file` was removed on 2026-08-28 because it
injected 111 unrelated variables, and `scripts/checks/check-env-file-scope.ps1` now
enforces its absence. The README paragraph was never updated with it.

Not fixed here: the item is a path move and the anchor puts new README content out of
scope. It belongs to `sl-readmes` (or `sl-closeout`'s doc-error count).

## 9. Two stale pointers inside the OB1 submodule

`git -C OB1 grep` finds:

- `docker/docker-compose.yml:192` - "Mirrors mnemory-gateway/app.py." (comment)
- `recipes/daily-digest/src/enrich/egress.ts:13` - "(search-gateway/searxng/settings.yml:
  `socks5h://tor:9050` ...)" (comment, and already stale on its own terms - tor was retired
  2026-08-21)

Both are comments, neither is executed, and bumping the gitlink is not in scope. Whoever
next opens an OB1 PR can carry them.

## 10. Smaller things, recorded so they are not re-derived

- `search/gateway/README.md` sits one directory deeper than before, so both of its
  relative links needed a `../`. The build-spec link
  (`../../documentation-plans-ai-stack/...`) is the dangerous one: at the new depth the old
  spelling resolves to `ai-stack/documentation-plans-ai-stack/`, a path inside this repo,
  so it would have become a broken link that still *looks* plausible. Now `../../../`.
- The CI job **name** `pytest-search-gateway` was deliberately kept. The anchor asks that
  "ci.yml's pytest-search-gateway job invokes that path", which names the job as it stands;
  renaming a job also renames a required check in branch protection.
- `.env` (gitignored, operator-owned) line 130 says "cloud services (Claude Code) use to
  reach mnemory-gateway" - the service, not a path. Left alone. Line 151 of the same file
  points at `documentation/web-search/integration-plan-private-search-gateway.md`, already
  dead before this item.
- Every other surviving `search-gateway` hit is the **container** name (`search-gateway`),
  the **image** / package name (`private-search-gateway`), or the archived plan's filename
  `integration-plan-private-search-gateway.md`. None of them are paths and none were
  renamed - the anchor forbids renaming services, containers, images and networks.
