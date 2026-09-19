# stack-layers `sl-colo-inference` — findings

**Item:** `sl-colo-inference` (PLAN 2.7, Part L.1, wave 2) — move `llm-queue/` and the
shared `config/` inference files into `inference/`, delete `config/`.
**Written:** 2026-09-19, by the developer, in worktree `wt-sl-colo-inference`
(branch `work/sl-colo-inference`, base `development` @ `9f64b84`).

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

The anchor requires the mount removed **with evidence** that no OWUI code path reads
`/app/config`, or narrowed. It was **removed**. The evidence, in the order it was taken:

1. [observed 2026-09-19] `git grep -n '/app/config'` over the whole repo except `OB1/`:
   **zero hits**. Nothing in `status-pipe/`, `entrypoint.sh`, `owui/` or any pipe names
   the path.
2. [observed 2026-09-19] In the **running** container:
   `docker exec openwebui sh -c 'grep -rIn "/app/config" /app/backend/open_webui /app/build'`
   → no output. No code in the image's Python backend or its built frontend bundle
   references it.
3. [observed 2026-09-19] `docker exec openwebui env | grep -i config` → no output. No
   environment variable in the live container points at `/app/config`.
4. [source, inside the image] `open_webui/env.py:222` resolves OWUI's own state from
   `DATA_DIR = Path(os.getenv('DATA_DIR', BACKEND_DIR / 'data'))`, i.e.
   `/app/backend/data` — the `openwebui-data` volume, which is mounted and untouched.
5. [observed 2026-09-19] A read-only scan of the live `webui.db` (all 44 tables, every
   column, `LIKE '%/app/config%'`) found the string only in `chat_message.content` (13),
   `chat_message.output` (11), `chat.chat` (10) and `file.data` (5) — i.e. **chat
   transcripts and uploaded files**, never `function`, `tool`, `model` or `config`.
   Reading the hits shows why: they are a pasted 2025 Ollama-era compose snippet
   (`- ./config:/app/config`) inside a saved conversation. That is user content, not a
   consumer.

**What this does NOT establish** [not verifiable here]: that no *future* OWUI version
reads `/app/config`, and that no OWUI feature reads it through a path this repo cannot
see. Both were bounded by checking the image actually deployed.

**What removal does not do:** the change is to `frontend/docker-compose.yml` only. The
**running** `openwebui` container still has the directory mounted — it was created before
this change — and will keep it until the next deliberate recreate of the frontend plane.
Nothing here restarts, rebuilds or retags anything (and the netns rule still applies:
never restart `openwebui` alone).

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
  uses). They are not resolved by any tool and were left as-is. `__init__.py:9` was
  re-wrapped for an unrelated reason — see F11.

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

## F11 — a pre-existing ruff E501 in a moved file, fixed because the file became this item's

[source] `llm-queue/src/llm_queue/__init__.py:9` was 103 characters against the
subproject's own `line-length = 100` (`pyproject.toml:40-46`). It was red on the base
commit — the fix lives on the unmerged `sl-closeout` branch — so `ruff check .` failed in
this worktree before any change of mine. The line was re-wrapped (the docstring's design
pointer, split across two lines with a note that the path is repo-root-relative).

This is a behaviour-free change to a file this item moved, and it is called out because it
means `ruff check .` going green here is **not** attributable to the move alone.

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
  and `src/llm_queue/__init__.py`, whose only change is the F11 line-wrap. **No executable
  line of llm-queue and no LiteLLM setting changed.**
