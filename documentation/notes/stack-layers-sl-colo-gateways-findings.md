# sl-colo-gateways - findings

Sink for harness item `sl-colo-gateways` (stack-layers PLAN.md section 2.7, wave 1).
Worker: `wt-sl-colo-gateways`, branch `work/sl-colo-gateways`.
Attempt 1 and 2 written 2026-09-19 on base `development` @ b28cbc5; **rewritten 2026-09-19
for attempt 3**, rebased onto `development` @ b9fff95 (six merges later).

**Every claim here was produced by running the command shown, in this worktree, on
2026-09-19.** That sentence is load-bearing: attempt 1's section 7 asserted a git fact
("that branch holds the blob") from memory, the tester ran `git rev-parse` and it was false,
and a reader who trusted the table would have passed on it. Nothing below is inferred, and
where a fact changed under the rebase it was re-measured rather than carried forward.

The item is three `git mv`s plus pointer repoints. These are the things it turned up that
are NOT part of it.

## 0. Three attempts, so the history reads straight

| | what | outcome |
|---|---|---|
| attempt 1 | `search-gateway/` -> `search/gateway/` verbatim, giving `search/gateway/gateway/src/gateway/` | 7/7 test cases passed; sent back at the release gate |
| attempt 2 | `search-gateway/gateway` -> `search/gateway`, `search-gateway/searxng` -> `search/searxng` | 7/7 passed; plan marked inadequate (P5, P6) |
| attempt 3 | same tree, rebased onto `development` b9fff95; plan and this note corrected | this submission |

Attempt 1 was the literal reading of the anchor's artifact line, and attempt 1's own section
4 recorded the double-nesting and named the alternative. The release gate took that option
and amended the anchor, also correcting acceptance criteria 1, 3 and 4, each of which
attempt 1 had reported as unrunnable or self-contradictory. The pipeline worked the way it is
meant to - the findings note is what carried the problem to the gate - so the detail is kept
rather than tidied away.

Attempt 3 exists because the base moved six merges (DECISIONS D18: a rebase that rewrites
commits is re-tested at the new tip, by the developer). Section 13 has the conflicts.

---

## 1. Attempt 1's acceptance criterion 1 named a file that has never existed (fixed in the amendment)

The original criterion was `git log --follow --oneline -- search/gateway/app.py`. There is no
`app.py` anywhere in the search gateway tree, on any branch: it is a package whose entry
module is `main.py`, and the `app.py` shape belongs to the *memory* gateway.

The amended anchor says `search/gateway/src/gateway/main.py` and adds
`search/searxng/settings.yml`. Both run. Measured on the rebased branch:

```
git log --follow --oneline -- search/gateway/src/gateway/main.py   -> 3  (oldest: 52b53cb)
git log --follow --oneline -- search/searxng/settings.yml          -> 5  (oldest: 52b53cb)
git log --follow --oneline -- memory/mnemory-gateway/app.py        -> 2
```

Each search file is one higher than attempt 1's count for the same content, because the
content crossed a second rename. `--follow` walks both.

## 2. Acceptance criterion 3's original command has never worked, on any branch (fixed in the amendment)

The original criterion was `python -m pytest search/gateway -q`. It fails - `13 failed, 24
passed` - and it fails identically on the pre-move layout. Falsified by extracting the old
base and running the equivalent there:

```
git archive b28cbc5 search-gateway | tar -x -C <scratch>/premove
cd <scratch>/premove && python -m pytest search-gateway -q
-> 13 failed, 24 passed, 2 warnings in 11.69s     (same 13 tests, same cause)
```

Cause: pointing pytest at the *parent* of the package means rootdir/inifile discovery walks
up from that argument and finds no config (there is no `pyproject.toml`, `setup.cfg` or
`tox.ini` at the repo root on either branch), so the package's own `asyncio_mode = "auto"`
never applies and every async test errors. The independent tester reproduced both sides and
reached the same conclusion (their finding A2).

The amended criterion is the working form, and it is what ci.yml now runs:

```
python -m pip install -e "./search/gateway[dev]"
python -m pytest search/gateway/tests -q        -> 36 passed, 1 deselected
```

Two pieces of evidence that the package's own config is genuinely in effect, rather than the
tests merely happening to pass: `1 deselected` is `addopts = "-m 'not integration'"` doing
its job, and the run writes its cache to `search/gateway/.pytest_cache/` - pytest chose
`search/gateway` as rootdir, which is where the `pyproject.toml` now sits.

Still true, still not fixed here: `python -m pytest <some-parent-dir> -q` remains broken for
this suite because there is no root pytest config. Fixing that means adding one, which is a
test-configuration change and belongs to `sl-manifest` / `sl-driver-parity`.

## 3. The `ruff check .` failure this note reported on the old base is FIXED on development

Attempt 1 and 2 recorded `ruff check .` as red on `development` @ b28cbc5:

```
E501 Line too long (103 > 100)  -->  llm-queue\src\llm_queue\__init__.py:9:101
```

`llm-queue/` carries its own `pyproject.toml` (`line-length = 100`, wider rule selection than
the root `ruff.toml`), and **cfa7d4e** - the 2026-09-18 plan-store move - had lengthened that
docstring's `Design:` pointer past the limit.

**259c972** ("docs: close CLEANUP-PLAN v3 and correct the seventeen claims a newcomer would
act on", merged as `be00d53` / `sl-closeout`) rewrote the line to
`Design: LiteLLM-Proxy/DESIGN-B2-inference-queue.md in the plan store` - 68 characters. On
the rebased branch `ruff check .` prints **"All checks passed!"**.

Recorded rather than deleted because the *shape* recurs: a path repoint in a docstring
silently breaks a lint gate that a subtree's own config, not the root config, enforces. This
item repoints paths in one docstring too (`openbrain-gateway/app.py:8`); `ruff check .` was
re-run after that edit for exactly this reason.

## 4. Why `search-gateway/` split into two directories instead of one

`search-gateway/` held two unrelated things: `gateway/` (a Python project - `pyproject.toml`,
`src/`, `tests/`, `Dockerfile`) and `searxng/` (two config files the *searxng* service
bind-mounts; the gateway never reads them). Moving the wrapper wholesale, as the original
artifact line said, carried the wrapper's name into the destination and produced
`search/gateway/gateway/src/gateway/` - four levels spelling "gateway", a build context of
`./gateway/gateway`, and an anchor whose own `pytest search/gateway` path named a directory
that was not the Python project.

The amended layout drops the wrapper: `search/gateway/` is the Python project (its
`README.md` and `.gitignore` came up with it) and `search/searxng/` is the engine's config,
beside the compose file that binds it. `search-gateway/` is gone.

## 5. The memory plane cannot be built from a worktree (pre-existing, unchanged)

`memory/docker-compose.yml` builds `mnemory` from `context: ../../mnemory` - the sibling
`mnemory` checkout beside `ai-stack/`, outside this repository. Rendered from a worktree it
resolves to `D:\Open WebUI\ai-stack\.claude\worktrees\mnemory`, which does not exist.
`config` renders it happily; only a `build` would fail. The service is pinned
`image: mnemory:local` + `pull_policy: never` precisely so nobody builds it by accident, and
`memory/README.md` documents the out-of-repo context.

The memory render has four path entries, not two. The other two, also pre-existing and
untouched: `mnemory-backup` binds `../backups/mnemory` (gitignored artifact output - absent
in a fresh worktree, created by compose on `up`) and `../backup/mnemory-backup.sh` (exists,
tracked). Attempt 1's test plan enumerated only some of them, which the tester flagged
(their P2); an enumeration a tester is asked to match has to be complete or say that it is
not.

## 6. The `README.md` repo-map row: two corrections, one merged before this one

On the old base the row read `... `search-gateway/`, `mnemory-cloud-gateway/`, ...` - and
`mnemory-cloud-gateway` is a **container** name; the directory has always been
`mnemory-gateway/`. `sl-closeout` fixed that independently on development, to
`` `mnemory-gateway/` (builds the `mnemory-cloud-gateway` container) ``.

The rebase conflicted on exactly that line and the resolution keeps both intents:
sl-closeout's parenthetical clarification, and this item's repointed paths -
`` `search/gateway/`, `memory/mnemory-gateway/` (builds the `mnemory-cloud-gateway`
container) ``. Neither correction was dropped. Section 13 has the full conflict list.

## 7. Four old-path hits stand in `documentation/evidence/research-trust/TEST-PLAN.md` - and attempt 1's reason for it was FALSE

**The correction first.** Attempt 1's note and test plan both claimed lines 25 and 369 had to
keep the old spelling because "that branch holds the blob at the old path; repointing breaks
the command". The tester checked; it is not true, and re-run here:

```
git rev-parse --verify work/research-trust
  -> fatal: Needed a single revision            (exit 128)
git for-each-ref --format='%(refname)' | grep -i research-trust
  -> (no output, exit 1 - no local ref, no remote-tracking ref)
```

The branch was deleted after its merge, per the repo's branch-hygiene rule. Those two
`git show work/research-trust:...` commands are dead at **either** spelling - exactly like
line 64, which attempt 1 itself described that way. I asserted a git fact I had not run, and
it was wrong. The lesson is in the amended anchor's out-of-scope list now.

**The true reason the four lines stay**, which survives the correction: that file is a
*completed record of a test run*. Its value is that it says what was actually executed, at
the paths that existed when it was executed. Rewriting a historical record to match a later
tree does not make it more useful; it makes it a record of something that never happened.

| line | what it is |
|---|---|
| 25, 369 | `git show work/research-trust:search-gateway/searxng/settings.yml` - the command that was run at the time, against a branch since deleted |
| 64 | a `cd` into `wt-research-trust/search-gateway/gateway`, a worktree that no longer exists |
| 462 | "the parent diff touches only `documentation/`, `search-gateway/`, ..." - an assertion about a diff taken in September, true of that diff |

The two lines in that file that are **live procedure** were repointed, because a reader would
run them today (the implementation-guide index still lists research-trust as "merged,
awaiting deploy"): line 801 (the image-bump rule, now `search/gateway/README.md`) and line
815 (the rollback `git checkout`, now `search/searxng/settings.yml`).

The amended criterion 4 adds `documentation/evidence/` to the exempt list with the reasoning
"completed test records name what was run", which is the general form of this.

## 8. `search/README.md` still describes an `env_file` that was deleted in 2026-08

Around line 160: "`gateway` additionally inherits the whole root `.env` through
`env_file: ../.env`". `search/docker-compose.yml` carries a fifteen-line comment at the
`gateway` service saying the opposite - the `env_file` was removed on 2026-08-28 because it
injected 111 unrelated variables, and `scripts/checks/check-env-file-scope.ps1` now enforces
its absence. The README paragraph was never updated with it, and `sl-closeout`'s doc-claim
sweep did not reach it either.

Not fixed here: the item is a path move and the anchor puts new README content out of scope.
It belongs to `sl-readmes`.

## 9. Three stale pointers inside the OB1 submodule

Attempt 1 said two; the tester found three (their N1), and re-run on the rebased gitlink
`git -C OB1 grep -n -e search-gateway -e mnemory-gateway` returns exactly these:

```
docker/docker-compose.yml:192   "Mirrors mnemory-gateway/app.py."
docker/docker-compose.yml:214   "stack where mnemory-gateway joins `default` because llm-net is"
recipes/daily-digest/src/enrich/egress.ts:13
                                "(search-gateway/searxng/settings.yml: `socks5h://tor:9050` ...)"
```

All three are comments, none is executed, and the `egress.ts` one is doubly stale on its own
terms (tor was retired 2026-08-21). Bumping the gitlink is out of scope; whoever next opens
an OB1 PR can carry them.

## 10. `CLEANUP-PLAN.md` now contradicts itself about this move - left alone deliberately

On the rebased tree (line numbers moved when `sl-closeout` closed the plan out):

```
CLEANUP-PLAN.md:943    Source trees (`little-coder/`, `search-gateway/`, `llm-queue/`, ...) do NOT move today.
CLEANUP-PLAN.md:1283   | `search-gateway/` -> `search/gateway/` | build context, searxng config bind, CI job |
```

Line 943 is now false (it moved), and line 1283 - the row that *plans* the move - names the
attempt-1 layout rather than the amended `search/gateway` + `search/searxng`. Both left
untouched: the anchor exempts `CLEANUP-PLAN.md` from the grep, and `sl-closeout` owns that
file. Note that `sl-closeout` has already merged, so nobody is holding these; they need an
owner. Raised by the tester as their N2.

## 11. DEPLOY STEP - the live `searxng` container still binds the OLD directory, rw

Not a defect in this item; the thing that has to happen at landing. **The orchestrator does
this at merge time, under the `search` lease** - not the developer, not the tester, and not
as part of a test pass. Verified read-only here with `docker inspect` (nothing was started,
stopped or restarted):

```
docker inspect searxng --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}} rw={{.RW}}{{"\n"}}{{end}}'
  -> bind D:\Open WebUI\ai-stack\search-gateway\searxng -> /etc/searxng rw=true
docker inspect searxng --format '{{.State.StartedAt}} {{.State.Status}}'
  -> 2026-09-13T07:29:28.598871536Z running
```

When this lands on the main checkout, `search-gateway\searxng` disappears from the working
tree while the running container keeps that host path mounted. Consequences:

- `docker restart searxng` does **not** repoint a bind. Only a recreate does:
  `docker compose -f search/docker-compose.yml --env-file .env up -d searxng`, under the
  `search` lease.
- Until that recreate, edits to `search/searxng/settings.yml` do not reach the live engine -
  it is serving config from a host path the tree no longer contains.
- Nothing is stranded in the old directory: it held only the two tracked files, both of which
  moved, so the recreate is clean.

Raised by the tester as their D1.

## 12. Smaller things, recorded so they are not re-derived

- **The one edit in this item that is not a path.** `ci.yml`'s `pytest-search-gateway` job
  changed from `pip install -e ...` / `pytest -q <path>` to `python -m pip install -e ...` /
  `python -m pytest <path> -q`. The `python -m` prefixes and the moved `-q` are a *command*
  change, not a path change: they are the amended anchor's criterion-3 command reproduced
  verbatim, so the gate and the CI job cannot drift apart. Everything else in the diff is a
  path.
- `search/gateway/README.md` sits one directory deeper than `search-gateway/README.md` did,
  so both of its relative links needed a `../`. The build-spec link
  (`../../documentation-plans-ai-stack/...`) is the dangerous one: at the new depth the old
  spelling resolves to `ai-stack/documentation-plans-ai-stack/`, a path inside this repo, so
  it would have become a broken link that still *looks* plausible. Now `../../../`. Its
  in-file dev commands moved with the package too - `pip install -e "gateway[dev]"` and
  `ruff check gateway/src gateway/tests` became `pip install -e ".[dev]"` and
  `ruff check src tests`, because that README now sits *in* the package rather than above it.
- The CI job **name** `pytest-search-gateway` was deliberately kept. The anchor asks that
  "ci.yml's pytest-search-gateway job invokes that path", which names the job as it stands;
  renaming a job also renames a required check in branch protection.
- `.env` (gitignored, operator-owned) says "cloud services (Claude Code) use to reach
  mnemory-gateway" - the service, not a path. Left alone.
- Every surviving `search-gateway` hit outside the exempt directories is the **container**
  name (`search-gateway`), the **image** / Python package name (`private-search-gateway`),
  the **CI job** name, or the archived plan's filename
  `integration-plan-private-search-gateway.md`. None is a path and none was renamed - the
  anchor forbids renaming services, containers, images and networks.
- `pip install -e "./search/gateway[dev]"` writes `src/private_search_gateway.egg-info/` into
  the tree wherever the venv lives, and pytest writes `search/gateway/.pytest_cache/`. Both
  are covered by `search/gateway/.gitignore`, so `git status --porcelain` stays empty - but
  they are real files, which is why the test plan has the tester confirm that rather than
  assume it.

## 13. The rebase onto b9fff95 - what conflicted, and how

Six merges landed between the original base (b28cbc5) and b9fff95: `sl-colo-portal`,
`sl-manifest`, `sl-inference-split`, `sl-closeout`, `sl-ob1-profiles`, `sl-frontend-solo`.
`git rebase development` replayed both commits; **one** file conflicted.

| file | what happened |
|---|---|
| `README.md` (repo map) | **Conflict.** `sl-closeout` had rewritten the same row (`mnemory-cloud-gateway/` -> `mnemory-gateway/ (builds the mnemory-cloud-gateway container)`); this item drops both directories from the root. Resolved by keeping both: `` `search/gateway/`, `memory/mnemory-gateway/` (builds the `mnemory-cloud-gateway` container) ``. Section 6 |
| `CLAUDE.md` | Auto-merged (the pointer line). But `sl-closeout` also ADDED prose this item's move falsifies: "`openbrain-gateway/` ... beside its twin `mnemory-gateway/`". After the move it is no longer beside it. Reworded to "`openbrain-gateway/`; its twin `memory/mnemory-gateway/` ... moved into the memory plane" - a path repoint plus the minimum words needed to keep the sentence true |
| `.env.example` | Auto-merged. This item's SEARXNG_IMAGE comment moved from line 86 to 188 under the new COMPOSE_PROFILES and inference sections; the comment itself is unchanged apart from the path |
| `.claude/skills/stack-map/references/workspace-stacks.md` | Auto-merged; the memory-gateway pointer sits at line 392 now |
| `documentation/implementation-guide/README.md` | Auto-merged; the `web-search/` row sits at line 31 now |
| `.github/workflows/ci.yml` | **No conflict** - `sl-driver-parity` (which adds a driver job) is not merged yet. Re-check at the next rebase |

**Citation sweep** (re-derived by construct, not by trusting the line numbers). This item's
edits to the two compose files are four in-place substitutions with **no net line change** -
`search/docker-compose.yml` 175 lines before and after, `memory/docker-compose.yml` 144 - so
every existing citation into them still resolves:

| citation | construct at that line, on the rebased tree |
|---|---|
| `stack.manifest.toml:208` -> `memory/docker-compose.yml:138-140` | `llm-net: external: true / name: ai-stack_llm-net` |
| `stack.manifest.toml:209` -> `memory/docker-compose.yml:36` | `- LLM_BASE_URL=http://llama-cpp:8080/v1` |
| `stack.manifest.toml:227` -> `search/docker-compose.yml:173-175` | `default: external: true / name: ai-stack_default` |
| `stack.manifest.toml:235` -> `search/docker-compose.yml:25-28` | `cap_add: - NET_ADMIN` + `devices: - /dev/net/tun:/dev/net/tun` |
| `.env.example:158` -> `memory/docker-compose.yml:111` | `- BACKUP_INTERVAL=${MNEMORY_BACKUP_INTERVAL:-86400}` |

No citation anywhere in the tree points into a file this item moved, and none needed
re-deriving. The three other `<plane>/docker-compose.yml:<line>` citations found tree-wide
are in other items' evidence files (`sl-manifest-test-plan.md`, `sl-closeout/test-plan.md`)
and in `documentation/notes/` - completed records, left alone, and unaffected because the
line numbers did not move.
