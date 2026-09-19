# stack-layers `sl-colo-inference` — findings

**Item:** `sl-colo-inference` (PLAN 2.7, Part L.1, wave 2) — move `llm-queue/` and the
shared `config/` inference files into `inference/`, delete `config/`.
**Written:** 2026-09-19, by the developer, in worktree `wt-sl-colo-inference`
(branch `work/sl-colo-inference`). **Revised 2026-09-19 after test attempt 1 passed**
(tester `wt-tester-colo-inf`, 12/12, plan judged inadequate): rebased from base `9f64b84`
onto `be00d53`, F2 corrected, F11/F12 updated for the rebase, **F13 and F14 added**.

> ### Read F14 first if you are about to merge this.
> Every other entry here is context for a later reader. **F14 is an action with a
> deadline**: six bind sources on four *running* containers point into the `config/`
> directory this item deletes, and the landing step is a compose recreate of the inference
> plane under its lease, not a `docker restart`.

Everything below is true but outside the artifact. Each entry says **how it is known**:

- **[source]** — read from a file in this tree; the file and line are named.
- **[observed]** — a command was run and its output is quoted; the date is given and it
  has not been re-run since.
- **[not verifiable here]** — a third party's behaviour; the reader is told to check
  before acting.

---

## F1 — the anchor's agent-org criterion rests on a premise that is false

The anchor's `artifact` field says "agent-org/docker/docker-compose.yml's mount of
config/litellm repointed", and the developer brief named lines ~118-125 as the place.

**agent-org has never mounted the shared `config/` directory.** [source] The only
`../config` in `agent-org/docker/docker-compose.yml` is line 522:

```
      - ../config/litellm-cloud.config.yaml:/app/config.yaml:ro
```

That file lives at `agent-org/docker/`, so `../config` is **`agent-org/config/`**, which
is agent-org's own configuration directory and contains exactly one file,
`litellm-cloud.config.yaml` — the separate, profile-gated cloud gateway (Pc.1, OD-6).
It is not the moved tree.

[observed 2026-09-19] The `agent-org` render is **byte-identical** before and after this
item (normalized JSON, `docker compose -f agent-org/docker/docker-compose.yml --env-file
agent-org/docker/.env config --format json`, compared against the same render from a
`git archive` of `development` @ `9f64b84`: `IDENTICAL`). With `--profile cloud --profile
workers` the one litellm bind resolves to
`<root>\agent-org\config\litellm-cloud.config.yaml`, which exists.

So the criterion is met vacuously: nothing to repoint. **One line in agent-org WAS
changed** and it is a comment, not a mount:
`agent-org/config/litellm-cloud.config.yaml:8` pointed at the moved file
(`config/litellm.config.yaml`) when warning that the local gateway is a different,
air-gapped instance; it now reads `inference/config/litellm.config.yaml`.

This is declared rather than quietly reinterpreted, per MERGE-PROTOCOL §2 ("if the work
shows an acceptance criterion is itself wrong, say so").

## F2 — the frontend's `/app/config` mount: what was checked, and what removal does not do

> **CORRECTED 2026-09-19, after test.** Evidence item 1 as first written said
> `git grep -n '/app/config'` returned **zero hits** in this repo. That was FALSE, and the
> same false sentence had been written into the deliverable
> (`frontend/docker-compose.yml`). The cause is a tooling trap, recorded separately as
> **F13**. The four load-bearing checks below (2-5, renumbered 1-4) are unaffected — they
> were run against the deployed image and its database, never through that grep — and the
> conclusion is unchanged. The corrected repo-side statement is item 5.

The anchor requires the mount removed **with evidence** that no OWUI code path reads
`/app/config`, or narrowed. It was **removed**. The evidence:

1. [observed 2026-09-19] In the **running** container:
   `docker exec openwebui sh -c 'grep -rIn "/app/config" /app/backend/open_webui /app/build'`
   → no output. No code in the image's Python backend or its built frontend bundle
   references it. Widened by the tester to all of `/app` except `data/`: the only files
   holding the string were `/app/config/litellm/assemble-config.py` and
   `/app/config/litellm.config.yaml` — **the mounted content itself**, not a consumer.
2. [observed 2026-09-19] `docker exec openwebui env | grep -i config` → no output. No
   environment variable in the live container points at `/app/config`.
3. [source, inside the image] `open_webui/env.py:222` resolves OWUI's own state from
   `DATA_DIR = Path(os.getenv('DATA_DIR', BACKEND_DIR / 'data'))`, i.e.
   `/app/backend/data` — the `openwebui-data` volume, which is mounted and untouched.
4. [observed 2026-09-19] A read-only scan of the live `webui.db` (all 44 tables, every
   column, `LIKE '%/app/config%'`) — this is where OWUI's tools, functions and pipes
   actually live, so it is the check that a repo grep and an image grep both miss. Hits
   only in `chat_message.content` (13), `chat_message.output` (11), `chat.chat` (10) and
   `file.data` (5). The tester counted the code-bearing tables explicitly: `function`
   (9 rows) **0**, `tool` (5 rows) **0**, `model` (147 rows) **0**, `prompt` (0 rows)
   **0**, `config` (358 rows) **0**. Opening a hit shows a `docker-compose.yml` example
   (`stt-service` / `nlp-service`, `- ./stt_config:/app/config`) inside a saved
   conversation — transcript text, not a loaded code path. *(The first version of this
   note called it "a 2025 Ollama-era compose snippet a user pasted"; it reads as an
   assistant-authored example. Same class, wrong provenance — corrected here.)*
5. [observed 2026-09-19, **corrected**] `MSYS_NO_PATHCONV=1 git grep -n '/app/config' -- . ':!OB1'`
   returns a **non-zero** count — 68 when the tester measured it, 100 once this revision's
   own prose was added, because the note and the compose comment now quote the string
   themselves. The count is therefore NOT the bar. Two stable bars are:
   **(a) the OWUI-side trees: zero**, and **(b) code outside documentation and this
   comment block: 27**, in twelve files, **none of them OWUI's**:
   - **zero** in the OWUI-side trees — `MSYS_NO_PATHCONV=1 git grep -n '/app/config' -- status-pipe owui entrypoint.sh Dockerfile.openwebui-gpu dockerfile.tailscale`
     → no output. That is the narrow claim the anchor's criterion needs, and it holds.
   - `little-coder`'s **own** `/app/config/little-coder.config.yaml`
     (`little-coder/src/littlecoder/config.py:27`, `daemon.py:1086`,
     `docker/Dockerfile.agent:46`, `docker/entrypoint-agent.sh:24`), mounted into
     *different containers from a different source* by `coder/docker-compose.yml:113`
     (`../little-coder/config`) and `agent-org/docker/docker-compose.yml:325,417`
     (`../agent-bridge/worker-configs/worker-{1,2}`).
   - the unrelated `/app/config.yaml` of the LiteLLM gateways
     (`inference/compose/gateway.yml`, `upstreams.yml`,
     `agent-org/docker/docker-compose.yml:520,522`).
   - `documentation/archive/` tutorials, and this item's own test plan, findings note and
     the `frontend/docker-compose.yml` comment block.
   ```
   MSYS_NO_PATHCONV=1 git grep -n '/app/config' -- . ':!OB1' ':!documentation' ':!frontend/docker-compose.yml'
     6 agent-org/docker/docker-compose.yml     5 inference/compose/gateway.yml
     2 coder/docker-compose.yml                2 inference/compose/upstreams.yml
     1 coder/README.md                         2 inference/config/litellm.config.yaml
     1 little-coder/docker/Dockerfile.agent    4 inference/config/litellm/assemble-config.py
     1 little-coder/docker/entrypoint-agent.sh 1 agent-org/docs/log/P8-org-self-knowledge.md
     1 little-coder/src/littlecoder/config.py
     1 little-coder/src/littlecoder/daemon.py
   ```
   The load-bearing bar is (a): zero in the OWUI trees.

**What this does NOT establish** [not verifiable here]: that no *future* OWUI version
reads `/app/config`, and that no OWUI feature reads it through a path this repo cannot
see. Both were bounded by checking the image actually deployed.

**What removal does not do:** the change is to `frontend/docker-compose.yml` only. The
**running** `openwebui` container still has the directory mounted — it was created before
this change — and will keep it until the next deliberate recreate of the frontend plane.
Nothing here restarts, rebuilds or retags anything (and the netns rule still applies:
never restart `openwebui` alone). **This is one of six such binds; see F14, which is the
one entry in this file a reader must act on before the merge lands.**

## F3 — the gateway-routing check is VACUOUS when run from a worktree

`scripts/checks/check-llm-gateway-routing.ps1:73` carries `'*\.claude\*'` in
`$allowPathLike`. [source] Agent worktrees live at
`<repo>\.claude\worktrees\wt-<id>\`, so **every file in a worktree matches that glob and
is allow-listed**, and `Test-Allowed` returns true for all of them before a single line is
read.

[observed 2026-09-19] Planting `api_base: http://llama-cpp-upstream:8080/v1` in this
worktree's `inference/config/litellm/model_list/local.yaml` and running the check from the
worktree root: `OK - no LLM gateway bypasses found`, exit 0. The same plant in a
`git archive` export outside `.claude`: `FAIL - 1 gateway bypass(es) found`, exit 1.

**Consequence for everyone, not just this item:** the pre-commit hook's check 3 proves
nothing about a commit made from a worktree. Any agent that reports "the routing check is
green" from a worktree has reported that the check did not run. This item's evidence was
therefore taken from an export; the test plan tells the tester to do the same.

Fixing the glob is **out of scope here** and is not obviously a one-liner: `.claude/` also
holds skills and settings that are not first-party service source, and narrowing it to
`'*\.claude\skills\*'`-style entries needs its own item and its own test.

## F4 — the llm-queue README's revert instruction was already wrong before this item

[source] On the base commit, `llm-queue/README.md:72` read:

```
1. `config/litellm.config.yaml`: both `qwen36-27b` `api_base` -> `http://llama-cpp-upstream:8080/v1`
```

`sl-inference-split` (merged the same day, `9f64b84`) moved the `model_list` **out** of
that file into `config/litellm/model_list/local.yaml`, leaving `litellm.config.yaml` as
the base config with no models in it. Following the instruction literally would have
edited a file that has no `api_base` to change and left the queue in the path.

Repointed here to `inference/config/litellm/model_list/local.yaml` with a parenthetical
saying when and why it moved. This is a **class-2** (a document instruction whose target
moved under it) of the kind the inference reviewer flagged.

## F5 — the same README's rebuild command has been inert since K.1

[source] `llm-queue/README.md:63` read
`docker compose build llm-queue && docker compose up -d llm-queue`. Since Part K.1
(2026-08-21) the root compose project is a **pure network anchor with zero services**, so
a bare `docker compose` from the repo root resolves `docker-compose.yml` at the root and
finds no `llm-queue` to build. Rewritten to name the plane file explicitly.

This is the MERGE-PROTOCOL case "for every command the artifact tells a reader to RUN,
check what the script actually does" — it was not caught by any path grep because the
command contains no path.

## F6 — the location-relative sweep: one real hit, and one deliberate non-hit

Every file moved by this item (32 under `llm-queue/`, 8 under `config/`) was swept for
`__file__`, `os.path.dirname`, `Path(__file__)`, `$PSScriptRoot`, `import.meta.url`,
`sys.path`, `pathlib` and `../`. Four lines matched, all `../` in prose:

- **The one real breakage** [source]: `llm-queue/README.md:14` was a markdown **link**,
  `../../documentation-plans-ai-stack/.../DESIGN-B2-inference-queue.md`. From
  `ai-stack/llm-queue/` that resolved to the sibling plan store; from
  `ai-stack/inference/llm-queue/` it would have resolved one directory short, to
  `ai-stack/documentation-plans-ai-stack/`, which does not exist. Corrected to `../../../`
  and the target confirmed present on disk. **This is the sl-colo-portal failure class
  exactly** (a location-relative path inside a moved file), reached here by sweeping
  rather than by a render.
- **The three non-hits** [source]: `llm-queue/pyproject.toml:4`,
  `llm-queue/src/llm_queue/__init__.py:9` and
  `config/litellm/model_list/local.yaml:23` write `../documentation-plans-ai-stack/...` in
  *prose*, using this repo's root-relative convention (the same spelling CLAUDE.md itself
  uses). They are not resolved by any tool and were left as-is. `__init__.py:9` is now
  `development`'s own wrapping of that docstring — see F11.

**The assembler is location-independent, which is why nothing had to change in it.**
[source] `config/litellm/assemble-config.py:55-57` takes all three of its paths from the
environment with **container-absolute** defaults (`/app/config.base.yaml`, `/app/conf.d`,
`/app/config.yaml`) and never derives a path from its own location. So the host-side move
cannot reach it; what had to stay correct is the compose **targets**, and those are
unchanged — only the bind **sources** moved (verified in the render diff).

## F7 — `stack.manifest.toml` carries drift this item did not create and did not fix

Three, all [source]:

1. `stack.manifest.toml:109` — `[planes.inference.profiles.local]` still has
   `pending = true   # sl-inference-split adds it`. That item **has landed** (it is in
   this item's base, `9f64b84`), so the flag is stale. It is not cosmetic:
   `scripts/stack/stack.py:648-657` reads `pending_profiles()` and prints "note: PENDING
   profiles enabled (...) - the compose files do not carry them yet, so enabling them
   changes nothing until the item that adds them lands" — which is now a false statement
   printed to the operator at the moment they enable the profile that does work.
2. `stack.manifest.toml:96-98` — the `host` requirements cite
   `inference/docker-compose.yml:83-86, 129-132`, `:34` and `:111,119`. Those services
   moved into `inference/compose/upstreams.yml` at sl-inference-split, so the line anchors
   point into the spine file, which is now comments and `include:`. Line 97 also says
   "sl-inference-split replaces it with LM_MODELS_DIR" in the future tense; it did.
3. `stack.manifest.toml:356,358,361` — three comments cite `config/caddy/Caddyfile:197`,
   `:143`, `:136`, `:295`. `sl-colo-portal` moved that tree to `portal/config/caddy/`
   (confirmed present at `portal/config/caddy/Caddyfile`); there is no `config/caddy/`.

None of these is in this item's artifact and none was touched. They belong to whoever
closes out sl-inference-split / sl-manifest / sl-colo-portal.

## F8 — there is no `stack-layers` status row in the implementation-guide index

[source] `documentation/implementation-guide/README.md` has 40-odd rows and no row for
`stack-layers`, though the plan set has lived in the plan store since 2026-09-18 and four
of its items (`sl-colo-portal`, `sl-manifest`, `sl-inference-split`, and this one) are
merged or in flight. CLAUDE.md's plan-store rule requires the one status row here for a
feature whose plan lives there, and `scripts/checks/plan-store.ps1` is documented to flag
"store features with no status row".

Not added by this item: the row describes the whole feature, several agents are working
in parallel on it, and a row added from four worktrees at once is four conflicts. It
belongs to the feature's closeout item, in one place, once.

## F9 — old paths kept on purpose in two places

1. `documentation/evidence/stack-layers/sl-inference-split-test-plan.md` and
   `sl-colo-portal-test-plan.md` contain `config/litellm...` and `llm-queue/...`. These
   are **historical execution records** of merged items — the commands a tester actually
   ran against the tree as it then stood. Rewriting them would make the record say
   something that was never run. Left untouched, deliberately.
2. `CLEANUP-PLAN.md` references the old paths (including `:1190`, which *is* the plan
   entry for this move). The anchor explicitly exempts it.

The anchor's grep criterion allows `archive/`, `notes/` and `CLEANUP-PLAN.md`; it does not
mention `documentation/evidence/`. That is a gap in the criterion's wording, declared here
rather than routed around.

## F10 — one substring collision the tester will see in the acceptance grep

The criterion greps for the literal `config/litellm`. `agent-org`'s own cloud gateway file
is named `config/litellm-cloud.config.yaml`, which **contains that substring**. Four lines
will match and all four are correct as written (they are `agent-org/`-relative — see F1):
`agent-org/docker/docker-compose.yml:522`, `agent-org/README.md:22`,
`agent-org/README.md:89`, `agent-org/IMPLEMENTATION-NOTES.md:331`.

## F11 — a pre-existing ruff E501, fixed twice; after the rebase the fix is NOT this item's

[source] `llm-queue/src/llm_queue/__init__.py:9` was 103 characters against the
subproject's own `line-length = 100` (`pyproject.toml:40-46`). It was red on this item's
ORIGINAL base (`9f64b84`), so `ruff check .` failed in this worktree before any change of
mine, and I re-wrapped the docstring's design pointer to clear it.

**That wrap is no longer in this branch.** `sl-closeout` fixed the same line differently
and merged to `development` first; the rebase onto `be00d53` (2026-09-19) surfaced it as
one of two prose conflicts, and I resolved it in **`development`'s favour** — the file now
carries sl-closeout's wording ("in the plan store (cloned beside this repo as
`../documentation-plans-ai-stack/implementation-guide`)") and nothing of mine.

Why this is worth a finding rather than a silent resolution: the earlier version of this
entry warned that `ruff check .` going green here was "not attributable to the move alone".
After the rebase that caveat is **gone** — the E501 fix arrives from the base, and
`git diff -M --stat development..work/sl-colo-inference -- '*llm_queue/__init__.py'`
shows `0` insertions and `0` deletions, i.e. a pure rename. (Without `-M` the same command
prints it as a new file, because the rename's other half is filtered out by the pathspec —
not a content change.) Anyone reading the pre-rebase evidence should
know the claim changed with the base, not with the code.

## F12 — what was deliberately not done

- No service, container, image tag, network, alias or volume name was changed. The
  normalized render diff for `inference` is **only** path values: with `--profile local`,
  seven `volumes[].source` plus one `build.context`; without the profile, five
  `volumes[].source` (the upstreams and the queue are not rendered).
- No container was started, stopped, restarted, rebuilt or retagged. No lease was taken
  (none is needed for a compose `config` render or a unit-test run).
- Of the 40 moved files, **34 moved byte-for-byte** (`git diff --cached -M --stat` shows
  `0` for them): `assemble-config.py`, `custom_callbacks.py`, `llama-swap.config.yaml`,
  `chat-template.jinja`, `llm-queue/Dockerfile`, `pyproject.toml` and all 26 Python
  sources, scripts and tests. The six with a delta are `litellm.config.yaml`,
  `litellm.ui.config.yaml`, both model-list fragments and `llm-queue/README.md` — comment
  and prose lines that named their own old path (plus the README's F4/F5/F6 corrections) —
  **No executable line of llm-queue and no LiteLLM setting changed.** After the rebase onto
  `be00d53` the count is **35 byte-for-byte**: `src/llm_queue/__init__.py` joined them,
  because the conflict on it resolved to `development`'s side (F11).

## F13 — `git grep '/app/config'` in Git Bash searches a path that is not in this repo

[observed 2026-09-19] On this host, **any** Git Bash command given a bare `/app/config`
argument receives something else:

```
$ python -c "import sys; print(sys.argv[1:])" /app/config
['C:/Program Files/Git/app/config']
```

MSYS path conversion rewrites an argument that *looks like* an absolute POSIX path into a
Windows path under the Git installation prefix, before the program ever sees it. So:

```
$ git grep -n '/app/config' -- . ':!OB1'                      # -> 0 hits, always
$ MSYS_NO_PATHCONV=1 git grep -n '/app/config' -- . ':!OB1'   # -> 68 hits
```

**The first form cannot return a hit in this repository, whatever the tree contains.** It
is not a narrow search; it is a search for a string that is not there.

This is why F2's evidence item 1 and the corresponding sentence in
`frontend/docker-compose.yml` both said "zero references" and were both wrong, and why the
test plan's T8a listed `expect: no output` as its PASS condition — the plan asked the
tester to confirm the output of a broken command. Found by the tester
(`wt-tester-colo-inf`), not by me; the substantive conclusion survived because the four
checks that carried it ran inside the container, not through this grep.

**The generalisable rule, and it is not specific to `/app/config`:** in Git Bash, any
argument beginning with `/` that is meant as *data* — a container path, a URL path, a
regex anchored on `/` — may be rewritten. Prefix `MSYS_NO_PATHCONV=1`, or write the
pattern so it does not start with a slash (`app/config`), and say which you did. A check
whose PASS condition is "no output" is exactly the shape where this is invisible: the
broken command and the true negative are indistinguishable.

[not verifiable here] Whether other hosts convert identically — MSYS conversion depends on
the Git-for-Windows build and `MSYS2_ARG_CONV_EXCL`. A reader on another machine should
run the two-line `python -c` demo above before trusting either form.

## F14 — ACT BEFORE THE MERGE LANDS: six live bind sources disappear when `config/` does

**This is the one entry in this file with a deadline.** Everything else is context; this
is an action for whoever merges.

[observed 2026-09-19, `docker inspect`, read-only] Four running containers bind six paths
under the directory this item deletes:

| container | host source (recorded in the container object) | destination |
|---|---|---|
| `llm-gateway` | `D:\Open WebUI\ai-stack\config\litellm.config.yaml` | `/app/config.yaml` |
| `llm-gateway` | `D:\Open WebUI\ai-stack\config\litellm\custom_callbacks.py` | `/app/custom_callbacks.py` |
| `llama-cpp-upstream` | `D:\Open WebUI\ai-stack\config\llama-swap.config.yaml` | `/app/config.yaml` |
| `llama-cpp-upstream` | `D:\Open WebUI\ai-stack\config\chat-template.jinja` | `/etc/llama/chat-template.jinja` |
| `llm-gateway-ui` | `D:\Open WebUI\ai-stack\config\litellm.ui.config.yaml` | `/app/config.yaml` |
| `openwebui` | `…/ai-stack/config` (whole directory) | `/app/config` |

(`llama-cpp-embed-upstream`, `llm-queue` and `llm-gateway-db` bind nothing under `config/`.)

**The mechanism.** The moment this merges and the operator's checkout updates,
`D:\Open WebUI\ai-stack\config\` ceases to exist. **A compose-file edit does not rewrite a
running container's `HostConfig`** — those six binds stay recorded on the existing
container objects, now pointing at absent sources. So what happens next depends entirely on
*how* the container is next brought up:

- **DANGEROUS — anything that starts the EXISTING container object.** A bare
  `docker restart llm-gateway` or `docker start`, and — the one nobody schedules — the
  automatic `restart: unless-stopped` after a Docker Desktop restart or a host reboot.
  These reuse the recorded bind spec. [not verifiable here] Docker's documented behaviour
  for a bind whose source does not exist is to **create an empty directory** at it; that
  would hand LiteLLM and llama-swap a *directory* where each expects a config **file**, and
  the failure surfaces as a config-parse error rather than as a missing mount. I did not
  reproduce this, because reproducing it means starting a container, which this item must
  not do. What IS certain from `docker inspect` is the first half: the source vanishes and
  the recorded spec is not updated.
- **SAFE — compose `up -d`, because it re-renders the *new* file and RECREATES the
  container with corrected sources.** Both automatic repair paths use exactly that, so
  they self-heal rather than break: [source] `scripts/checks/stack-watchdog.ps1:640`
  repairs any unhealthy container via `Invoke-PlaneCompose -Container $Container -Action
  @('up','-d')`, which builds `docker compose <plane args> up -d <service>`
  (`stack-watchdog.ps1:174,179`); `scripts/recovery/emergency-recovery.ps1:600` runs
  `docker compose -f $Script:InferenceCompose --env-file .env up -d llm-queue llm-gateway`,
  and `Start-InferenceStack` (`:221-228`, called at `:802`, `:960`, `:1023`) runs the same
  command for the whole plane.
- **NOT a repair — compose `restart`.** `docker compose restart` restarts the EXISTING
  container without re-rendering it, so it is in the dangerous group, not the safe one.
  `stack-watchdog.ps1:747` uses it — but only for `llama-cpp-embed-upstream`, which
  [observed] binds **nothing** under `config/`, so that call site is harmless here. The
  distinction matters more than the call site: "it goes through compose" is not the
  property that saves you; "it recreates the container" is.

**So the realistic failure is a reboot, or a hand `docker restart`, in the window between
the merge and the first compose recreate** — bounded, self-healing a watchdog cycle later,
and entirely avoidable.

**The landing step this item therefore requires**, as part of the merge and not as a
follow-up:

1. Take the `inference` lease (`lease.ps1 -Acquire -Name inference`).
2. From the repo root:
   `docker compose -f inference/docker-compose.yml --env-file .env up -d`
   — recreates `llm-gateway`, `llm-gateway-ui` and `llama-cpp-upstream` against the new
   sources.
3. The frontend's `openwebui` at its **next deliberate recreate** under the frontend's own
   rules — never `openwebui` alone; order openwebui → tailscale. Its mount is being
   *removed*, not repointed, so it carries no config-parse hazard; it is the one of the six
   that can wait.

**And the recreate deploys two items, not one.** [observed 2026-09-19] The running
`llm-gateway` mounts only `/app/config.yaml` and `/app/custom_callbacks.py` — it has no
`/app/conf.d`, no `/app/assemble-config.py`, and does not use `/app/config.base.yaml`. It
therefore **predates `sl-inference-split`** (merged at `9f64b84`, the same day). That is not
this item's doing, but whoever performs the recreate above will bring the gateway's
config-assembly mechanism live at the same time, and should expect the assembler's startup
log (`docker logs llm-gateway | grep assemble-config`) to be new output, not a regression.
