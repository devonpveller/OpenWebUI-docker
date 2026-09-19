# stack-layers `sl-colo-inference` — findings

**Item:** `sl-colo-inference` (PLAN 2.7, Part L.1, wave 2) — move `llm-queue/` and the
shared `config/` inference files into `inference/`, delete `config/`.
**Written:** 2026-09-19, by the developer, in worktree `wt-sl-colo-inference`
(branch `work/sl-colo-inference`). **Revised 2026-09-19 after test attempt 1 passed**
(tester `wt-tester-colo-inf`, 12/12, plan judged inadequate): rebased from base `9f64b84`
onto `be00d53`, F2 corrected, F11/F12 updated for the rebase, **F13 and F14 added**.
**Revised again after attempt 2 FAILED** on T9 and T13c, both caught by this item's own
cases: **F14's classification of repair paths was wrong** (corrected in place, see the
admonition inside it) and **F15 added** for the stale pointer the rebase carried in.
**Revised a third time 2026-09-19** after a review rejection (F7 rewritten as F7a/F7b) and
a fourth after rebasing onto **`b9fff95`** (`sl-frontend-solo`), which replaced
`frontend/docker-compose.yml` wholesale: **every line number in F7a and in F14's citations
of the recovery scripts was re-derived a second time** against the 484-line merged file and
the grown `emergency-recovery.ps1` / `stack-watchdog.ps1`. F7b lost an entry because
someone fixed it; the note says so rather than dropping it silently.

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
`agent-org/config/litellm-cloud.config.yaml:8-9` pointed at the moved file
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

**The mount was removed from BOTH openwebui definitions** (corrected 2026-09-19, third
review). `sl-frontend-solo` split the service in two — `openwebui` under `profiles:
[gpu]` and `openwebui-stock` under `profiles: [stock]`, the fresh-clone deployment — and
the rebase that brought that split in carried `- ../config:/app/config:ro` into the NEW
service while this item had removed it only from the old one. The four checks above are
about the OWUI image and its database, so they apply identically to both definitions:
same image release, same `openwebui-data` volume, same `webui.db`. `openwebui-stock`
now carries a one-line pointer at `frontend/docker-compose.yml:136` back to the six-line
comment on the `gpu` definition, rather than a second copy of it.

**What removal does not do:** the change is to `frontend/docker-compose.yml` only. The
**running** `openwebui` container still has the directory mounted — it was created before
this change — and will keep it until the next deliberate recreate of the frontend plane.
Nothing here restarts, rebuilds or retags anything (and the netns rule still applies:
never restart `openwebui` alone). **This is one of six such binds; see F14, which is the
one entry in this file a reader must act on before the merge lands.**

## F3 — the gateway-routing check is VACUOUS when run from a worktree

`scripts/checks/check-llm-gateway-routing.ps1:77` carries `'*\.claude\*'` in
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

## F7 — line citations: the eight this item BROKE and fixed, and the drift it only found

> **REWRITTEN 2026-09-19 after review rejection (`-Misfits`).** The previous version of
> this entry was headed "drift this item did not create and did not fix" — and the item had
> just created eight stale citations of its own, by inserting a 30-line evidence comment
> into `frontend/docker-compose.yml` and a line into `inference/compose/backups.yml`. That
> heading is the notes-lied failure class this repo keeps paying for: a findings file that
> reports someone else's debt while hiding the author's. Both halves are below, separated,
> and every line number in them was re-derived by opening the target.

### F7a — drift this item CREATED, and has fixed

> **RE-DERIVED A SECOND TIME, 2026-09-19.** The table below was first built against a
> 295-line `frontend/docker-compose.yml`. `sl-frontend-solo` then merged to `development`
> (`b9fff95`), replacing that file with a 479-line profiled one and re-deriving every
> citation into it for ITSELF. Rebasing onto that base put this item's six-line comment
> back in at **`frontend/docker-compose.yml:182`**, making the merged file **484** lines —
> so every citation at `:183` or below-in-file gained **+5** again, including the ones
> `sl-frontend-solo` had just fixed. The numbers here are the SECOND re-derivation, taken
> by opening the 484-line file. None of the first round's numbers survives; they are gone
> rather than corrected, because a superseded mapping in a findings note is a trap.

Inserting lines into a file silently invalidates every `file:line` citation below the
insertion point, anywhere in the tree — and it does so again every time the base moves.
This item's only insert into a cited file is the six-line evidence comment at
`frontend/docker-compose.yml:182` (+5). Everything at `:183` and beyond shifts.

Re-derived here [source, each by opening the target at the new line]:

| citing | cites | resolves to |
|---|---|---|
| `stack.manifest.toml:133` | `:473-481`, and `frontend_owui-net (:484)` | `default:` … `name: ai-stack_app-net`; the project-owned net |
| `stack.manifest.toml:147` | `:311-336` | the env-override block, `LLAMA_CPP_HOST` → `QUARTZ_TS_PORT` |
| `stack.manifest.toml:148,150` | `:311`, `:314`, `:313` | `LLAMA_CPP_HOST`, `LLAMA_CPP_EMBED_HOST`, `LLAMA_CPP_ENABLED` |
| `stack.manifest.toml:153` | `:246` | `SEARXNG_QUERY_URL` |
| `stack.manifest.toml:155` | `:317`, `:319`, `:318`, `:320-321` | the five `OPEN_NOTEBOOK_*` vars |
| `stack.manifest.toml:162` | `:333`, `:335`, `:323-332` | `QUARTZ_HOST`, `QUARTZ_ENABLED`, the wiki-route comment |
| `stack.manifest.toml:169` | `:291-302` | the tailscale service's one-variable block |
| `stack.manifest.toml:178` | `:158-165` **unchanged**, `:257-263` | the `gpu` build block (above the insert); the NVIDIA reservation |
| `stack.manifest.toml:179`, `:203` | `:290` | `network_mode: service:openwebui` |
| `stack.manifest.toml:197` | `:115-153` **unchanged** | the whole `stock` service (above the insert) |
| `stack.manifest.toml:200` | `:158-165` **unchanged**, `:257-263` | as `:178` |
| `.env.example:162` | `:375` | `BACKUP_INTERVAL=${OPENWEBUI_BACKUP_INTERVAL:-86400}` |

Four citations needed **no** change because they point ABOVE the insertion point
(`:115-153`, `:158-165` twice). Checking that and leaving them alone is as much part of the
method as renumbering the rest — a sweep that adds 5 to everything is wrong in four places.

Four more, in other items' records, shifted by the same insert and re-derived the same way
[source]:

| citing | cites | resolves to |
|---|---|---|
| `documentation/evidence/sl-closeout/test-plan.md:221` | `:375` | `BACKUP_INTERVAL=${OPENWEBUI_BACKUP_INTERVAL:-86400}` |
| `documentation/notes/stack-layers-sl-closeout-findings.md:70` | `:373` | `RETAIN_COUNT=${OPENWEBUI_BACKUP_RETAIN_COUNT:-2}` |
| `documentation/notes/cleanup-branch-closeout-audit-2026-09-19.md:104` | `:257-263` | `deploy:` … `capabilities: [ gpu ]` |
| `documentation/evidence/stack-layers/sl-manifest-test-plan.md:112,119,131,132,133,134` | `:317`, `:473-481`, `:311/:313/:314`, `:246`, `:317/:319/:318/:321`, `:333/:335/:323-332` | the same anchors |

> **A judgement call, and `sl-frontend-solo` made the OPPOSITE one — the gate should settle
> it.** Two of those four are `documentation/evidence/` **execution records of merged
> items**, and this item's own T9 declares such records are not rewritten. I update their
> line NUMBERS anyway, reasoning that a number there is *navigation*, not a claim: the
> sentence still asserts exactly what it asserted, about the same line of code. Changing
> what a record says was CHECKED would be falsification; changing where to look is not.
> `sl-frontend-solo` reached the other conclusion — its findings note (`:170-177`) says
> those files are "**deliberately not edited** - they are other items' notes and rewriting
> another item's evidence is not this item's business", and lists them for a later
> `sl-readmes` sweep. **Consequence, and it is the reason to declare this rather than
> quietly proceed:** that note's table is now obsolete — the two files it lists carry
> re-derived numbers, not the pre-profile ones it records. Whoever runs that sweep should
> re-derive rather than apply the table. I did not edit their note to say so, which would
> have been the same over-reach in the other direction.

**Citations SAVED rather than renumbered — the better outcome, applied to three files.**
Three of this item's edits were comment rewraps that had grown their file for no reason;
each was re-wrapped to be **line-neutral** instead:

| file | `git diff --numstat development --` | what that saves |
|---|---|---|
| `inference/compose/backups.yml` | `1 1` | `.env.example:333` (`:41,75,82-84`), `.env.example:346` (`:13,31`), `documentation/notes/stack-layers-sl-closeout-findings.md:282` (both sets), `…/sl-inference-split-test-plan.md:430` (`:39`) — all **verified correct** by opening each line |
| `inference/compose/queue.yml` | `3 3` | `stack.manifest.toml:120` (`queue.yml:39`, `profiles: [local]`) — **verified correct** |
| `inference/compose/upstreams.yml` | `4 4` | `stack.manifest.toml:120` (`:41`, `:130`, both `profiles: [local]`) — **verified correct**. Its other two citations are stale for a reason that is not this branch's: see F7b item 3 |

> **`queue.yml` is here because attempt 2 failed on it, and because this paragraph used to
> say the opposite.** It read: "`inference/compose/gateway.yml` (+1) and
> `inference/compose/queue.yml` (+1) are cited by nothing outside this item's own note and
> plan". That was **false for `queue.yml`** — `stack.manifest.toml:119-120` cites it — and
> the +1 pushed `profiles: [local]` from `:39` to `:40`, a citation correct on the base and
> broken by this branch. The sentence was false because the sweep behind it was: the
> manifest names the file as a **bare basename** (`…upstreams.yml:41,:130, queue.yml:39 and
> backups.yml:48`), the second and later items of a list whose first item carried the path,
> and a path-anchored grep saw one of three. The fix is `queue.yml` line-neutral like the
> other two, so nothing outside the plane needed touching at all; the sweep is fixed in F7c.

**Where a shift buys nothing, not shifting beats renumbering** — fewer files touched, fewer
conflicts for the branches landing beside this one, and nothing to get wrong. It is also
the only class of fix a base change cannot make stale: these survived both the
`sl-frontend-solo` and `sl-colo-gateways` rebases without a second look, while every
renumbered citation had to be re-derived again.

**The remaining shifted files carry no incoming citations — re-checked in BOTH forms**
(`git grep -nE '(^|[^/A-Za-z0-9_-])<basename>:[0-9]'` as well as the path form):
`scripts/checks/check-llm-gateway-routing.ps1` (+4),
`.claude/skills/stack-map/references/workspace-stacks.md` (+1),
`agent-org/config/litellm-cloud.config.yaml` (+1) and `inference/compose/gateway.yml` (+1).
`gateway.yml` returns **zero** hits either form, which is why its +1 is left alone. The
first two are cited only by this item's own note and plan and are re-derived there
(`check-llm-gateway-routing.ps1:77`, `litellm-cloud.config.yaml:8-9`).
`inference/docker-compose.yml` (+5) is the exception and it is F7b's, not F7a's.

### F7b — drift this item only FOUND (pre-existing; not touched)

[source, re-derived on `b9fff95`] Stale **before** this item, and stale for a different
reason — the files they point into were restructured, so renumbering would not fix them:

1. `stack.manifest.toml:107-109` — the inference `host` requirements cite
   `inference/docker-compose.yml:83-86, 129-132`, `:34` and `:111,119`. Those services
   moved into `inference/compose/upstreams.yml` at sl-inference-split, and the spine file
   is now ~90 lines of comments and `include:` — so `:111`, `:119` and `:129-132` are
   **past its end** (it is **84 lines on `development`**, i.e. they were already past the
   end before this branch existed) and `:83-86`, `:34` land on unrelated comments. Line
   `:108` also says "sl-inference-split replaces it with LM_MODELS_DIR" in the future
   tense; it did.
2. `stack.manifest.toml:421,423,426` — three comments cite `config/caddy/Caddyfile:197`,
   `:143`, `:136`, `:295`. `sl-colo-portal` moved that tree to `portal/config/caddy/`
   (confirmed present at `portal/config/caddy/Caddyfile`); there is no `config/caddy/`.
3. `documentation/notes/stack-layers-sl-inference-split-findings.md:194` and
   `documentation/evidence/stack-layers/sl-inference-split-test-plan.md:428` both cite
   `inference/compose/upstreams.yml:66,75` for the two
   `/models/lmstudio-community/…gguf` model paths. Those paths are at **`:69` and
   `:78`** — on this branch **and on `development`**, where the file is 169 lines on
   both sides and this branch's edit to it is line-neutral (`4 4`). So the drift is
   real and it is **not this branch's**: something grew `upstreams.yml` between
   sl-inference-split and now. **An earlier version of F7a claimed these two “stay
   correct untouched” because the edit was line-neutral.** Line-neutral means this
   branch did not break them; it does not mean they were right. Correcting the reason
   as well as the claim is the point — a true headline on a false mechanism misleads
   the next reader exactly as much as a wrong one.

**One entry has been REMOVED from this list because someone fixed it.** The first version
of F7b reported that `[planes.inference.profiles.local]` still carried
`pending = true   # sl-inference-split adds it`, making `scripts/stack/stack.py:709-719`
print a false "PENDING profiles enabled" note (`:713`, `:717-718`) to the operator. On
`b9fff95` that line reads `# NOT `pending`: sl-inference-split landed it. `profiles:
[local]` is live in …` (`stack.manifest.toml:119`) — corrected by one of the items that
landed in between. Recording the fix rather than silently dropping the entry is the point:
a findings note that quietly loses an item leaves the next reader unable to tell whether it
was fixed or forgotten.

**The scale, measured after the second re-derivation** [observed 2026-09-19]: the tree
holds **569** resolvable `path:line` citations, **141** of them into files this branch
changes. Nine resolve past their target's end, and **none is this branch's**: eight cite
`inference/docker-compose.yml` at `:111`, `:316`, `:320`, `:383`, `:448` — beyond its
**91 lines on `development`**, so already dead before this branch — and the ninth
(`documentation/notes/u6dark-findings.md:1074` → `README.md:184`) is into a file whose
length this branch does not change (168 lines on both sides).

Neither remaining entry is in this item's artifact and neither was touched. **This item
deliberately did not renumber them**, because a citation pointing at the wrong FILE is not
repaired by moving its line number, and silently rewriting another item's open debt hides
it.

### F7c — the general lesson, which is why the test plan now has a case for it

A colocation item's diff is reviewed hunk by hunk, and every hunk can be correct while the
commit as a whole breaks a citation nobody's eye passes over: the broken file is one the
diff does not touch. **Inserting N lines into a file is an edit to every citation into that
file below the insertion point**, and the only way to see it is to enumerate them from the
tree. The plan's T14 does that by construct and fails on any stale citation this branch
caused.

**And citations do not all name their file the same way.** Attempt 2 failed on exactly
this: `stack.manifest.toml:119-120` reads

```
# inference/compose/upstreams.yml:41,:130, queue.yml:39 and backups.yml:48
```

— one path-form citation followed by two **bare basenames**, which is how anyone writes a
list. A path-anchored `git grep 'inference/compose/queue\.yml:[0-9]'` sees one of the
three and reports a clean sweep. **Enumerate both forms**, and treat an ambiguous basename
as a finding rather than guessing which file it means:

```bash
git grep -nE '(^|[^/A-Za-z0-9_-])queue\.yml:[0-9]' -- . ':!OB1'   # and each other basename
```

The repo has several genuinely ambiguous basenames — `README.md`, `config.py`,
`pyproject.toml`, `docker-compose.yml` each name five or more files — so a resolver must
report those for a human rather than pick one. The third defence is the one that needs no
sweep at all: **make the edit line-neutral** and the citation cannot go stale.

**The `documentation/evidence/` question is settled.** Earlier revisions of this item
declared a gap: the anchor exempts `archive/`, `notes/` and `CLEANUP-PLAN.md` from the
stale-pointer criterion but does not name `documentation/evidence/`, and this item both
left merged items' execution records alone (T9) and updated their line numbers (F7a).
**The gate ruled: the exempt list is read as including `documentation/evidence/` for
completed records.** So a stale path in a merged item's evidence file is not a failure of
this item; updating a line NUMBER in one, as F7a does, remains a judgement the gate has
seen and accepted rather than an obligation. The same reasoning produced the six-line cap on the compose comment: evidence
belongs in this file, not in the deliverable (CLAUDE.md, "findings go to
`documentation/notes/`"), and a 30-line comment was both a policy breach and the mechanism
of the breakage.

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

- **DANGEROUS — anything that starts the EXISTING container object**, because it reuses the
  recorded bind spec. That is a bare `docker restart` / `docker start`, **`docker compose
  restart`** (which restarts the existing container and does NOT re-render the compose
  file), and — the one nobody schedules — the automatic `restart: unless-stopped` after a
  Docker Desktop restart or a host reboot.
  [not verifiable here] Docker's documented behaviour for a bind whose source does not
  exist is to **create an empty directory** at it. For the five FILE binds that hands
  LiteLLM and llama-swap a *directory* where each expects a config file, and the failure
  surfaces as a config-parse error rather than as a missing mount. I did not reproduce it,
  because reproducing it means starting a container, which this item must not do. What IS
  certain from `docker inspect` is the first half: the source vanishes and the recorded
  spec is not updated. **`openwebui` is the exception** — its bind is the whole `config/`
  *directory*, so an empty directory is exactly what it would get, and nothing reads
  `/app/config` anyway (F2). Of the six binds, five are hazardous and one is inert.
- **SAFE — compose `up -d`, because it re-renders the *new* file and RECREATES the
  container with corrected sources.** [source] `scripts/checks/stack-watchdog.ps1:700`
  repairs any unhealthy container via `Invoke-PlaneCompose -Container $Container -Action
  @('up','-d')`, which builds `docker compose <plane args> up -d <service>`
  (`stack-watchdog.ps1:162,174,179`); `emergency-recovery.ps1:646` runs
  `docker compose -f $Script:InferenceCompose --env-file .env up -d llm-queue llm-gateway`;
  and `Start-InferenceStack` (`:263-270`, called at `:852`, `:1014`, `:1081`) runs the same
  command for the whole plane.

> ### The recovery script's FIRST move is in the dangerous group. Corrected 2026-09-19.
>
> An earlier version of this entry put `compose restart` in the dangerous group but named
> `stack-watchdog.ps1:807` as its only call site and called that harmless (it targets only
> `llama-cpp-embed-upstream`, which [observed] binds nothing under `config/` — still true).
> **It missed the call sites that matter**, found by the tester running this item's own
> T13c as written. In `scripts/recovery/emergency-recovery.ps1`, `Invoke-MinimalRecovery`
> (`:602`) does:
>
> ```
> :624   docker compose -f $InferenceCompose --env-file .env restart llama-cpp-upstream llama-cpp-embed-upstream
> :626   docker compose -f $FrontendCompose  --env-file .env restart openwebui
> :633   docker compose -f $FrontendCompose  --env-file .env restart tailscale
> ```
>
> `llama-cpp-upstream` and `openwebui` are both in the table above. And this is not a
> peripheral branch: `Invoke-MinimalRecovery` is **the first thing both recovery modes
> try** — `recover` at `:777` and `nuclear` at `:959`, each gated only on
> `Test-BasicConnectivity`. The healing `up -d` at `:646` is 22 lines later in the same
> function and covers **only `llm-queue` and `llm-gateway`** — never `llama-cpp-upstream`.
> So in the window between the merge and the first recreate, `emergency-recovery.ps1
> recover` is itself a way to break the inference plane.
>
> **Read to the end of the function before deciding how bad that is.**
> `Invoke-MinimalRecovery` finishes by calling `Test-BasicConnectivity` again (`:666`),
> which at `:562` execs a real health probe into `llama-cpp-upstream`
> (`docker exec llama-cpp-upstream curl -f -s http://localhost:8080/health`). A broken
> upstream therefore makes minimal recovery return `$false`, the caller falls through to
> full (or nuclear) recovery, and *that* path's `Start-InferenceStack` does `up -d` for the
> whole plane and repairs it. **So the script does not leave the plane wedged and does not
> report a false success** — but it breaks the upstream first, drags `openwebui` and its
> netns companion `tailscale` through a restart cycle, waits 60 s, and then escalates the
> operator into a full teardown they did not need. `tailscale` itself is unaffected by this
> item (it binds `data/tailscale`, not `config/`).
>
> The general rule, which is what the earlier version got wrong: **"it goes through
> compose" is not the property that saves you — "it recreates the container" is.**

**So the realistic failure is a reboot, a hand `docker restart`, or an
`emergency-recovery.ps1 recover`, in the window between the merge and the first compose
recreate** — recoverable, but not something to leave lying around.

**The landing step this item therefore requires**, as part of the merge and not as a
follow-up. **Do it BEFORE anyone runs `emergency-recovery.ps1 recover` or `nuclear`** —
until the plane is recreated, those are a way to break it, not a way to fix it:

1. Take the `inference` lease (`lease.ps1 -Acquire -Name inference`).
2. From the repo root, with the operator's `COMPOSE_PROFILES` in `.env` (the real
   deployment runs `COMPOSE_PROFILES=local`; without it this brings up the gateway trio
   only and leaves `llama-cpp-upstream` on its stale spec):
   ```
   docker compose -f inference/docker-compose.yml --env-file .env up -d
   ```
   Compose recreates exactly the containers whose spec changed, so this covers
   `llm-gateway`, `llm-gateway-ui` and `llama-cpp-upstream`. Verify rather than assume:
   ```
   docker inspect -f '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}{{"\n"}}{{end}}{{end}}' \
     llm-gateway llm-gateway-ui llama-cpp-upstream
   ```
   — every source must now read `…\ai-stack\inference\config\…`, and `llm-gateway` must
   have gained `/app/conf.d` and `/app/assemble-config.py` (see the closing paragraph).
3. The frontend's `openwebui` at its **next deliberate recreate** under the frontend's own
   rules — never `openwebui` alone; order openwebui → tailscale. Its mount is being
   *removed*, not repointed, and nothing reads `/app/config` (F2), so it is the one of the
   six that can wait.
4. **Do not "fix" the `docker compose … restart` in `inference/llm-queue/README.md`'s
   Revert section on the strength of this entry.** That one is correct as written: it
   reverts by EDITING THE CONTENTS of two config files that stay where they are, so the
   binds resolve and a restart is the right, cheap verb. The hazard here is a bind whose
   SOURCE PATH no longer exists, which is a different thing entirely and applies only in
   the pre-recreate window.

**And the recreate deploys two items, not one.** [observed 2026-09-19] The running
`llm-gateway` mounts only `/app/config.yaml` and `/app/custom_callbacks.py` — it has no
`/app/conf.d`, no `/app/assemble-config.py`, and does not use `/app/config.base.yaml`. It
therefore **predates `sl-inference-split`** (merged at `9f64b84`, the same day). That is not
this item's doing, but whoever performs the recreate above will bring the gateway's
config-assembly mechanism live at the same time, and should expect the assembler's startup
log (`docker logs llm-gateway | grep assemble-config`) to be new output, not a regression.

## F15 — a rebase can carry a stale pointer INTO a branch without it appearing in the diff

[observed 2026-09-19] `.env.example:281` reached test attempt 2 reading
`# general_settings in config/litellm.config.yaml` — a live pointer, in the operator-facing
template, at a file this item had deleted. It was not a miss in the original sweep:

```
git grep -n 'config/litellm' 9f64b84   -- .env.example   ->  :265   (one hit)
git grep -n 'config/litellm' development -- .env.example ->  :271, :281   (two)
```

`sl-closeout` added the J.1 master_key NOTE paragraph and merged to `development` first.
When this branch rebased from `9f64b84` onto `be00d53`, that paragraph arrived **as
context** — `git diff -M development..work/sl-colo-inference -- .env.example` shows only
the OpenRouter hunk, because the J.1 paragraph is identical on both sides. So every
diff-shaped check passed, and the line was stale the whole time.

**The general shape, which is not specific to this item:** a colocation item's correctness
condition is *"no pointer in the TREE names the old path"*, but the natural thing to review
is the DIFF. Those are the same thing only while the base holds still. Any long-lived
branch that rebases — and under this repo's worktree-per-session policy most of them do —
can inherit a pointer at something it has itself moved, from work that landed in between.

**What actually catches it:** re-running the unbounded grep **against the tree** after every
rebase. Nothing else here does. `scripts/checks/check-llm-gateway-routing.ps1` does not look
for moved paths; `check-project-configs.ps1` renders compose and would not have noticed a
comment; `check-doc-placement.ps1` is about where plans live. The item's own test plan (T9)
is the only gate, which is why it is now the first line of that case and why the case names
`.env.example:271` and `:281` explicitly rather than trusting a sweep to surface them.

[source] Scope, re-checked independently by me after the tester reported it: sweeping both
the forward-slash and backslash forms of `config/litellm`, `config/llama-swap`,
`config/chat-template` and `llm-queue/` over the whole tree outside the exempt set returns
**nothing** once `:281` is repointed. `.env.example:281` was the only one.

**Not proposed here:** a check. A guard that re-greps for "paths this branch moved" would
need to know what the branch moved, which is a per-item fact, not a repo-wide one. The
honest fix is the discipline: **after a rebase, re-run the item's own tree-level sweep**,
and say in the evidence that you did.

## F15b — a rebase can add a SERVICE to a file you already edited, and your removal is
not re-applied to it

[observed 2026-09-19] The same base-moves-under-you shape as F15, one level up. This item
removed `- ../config:/app/config:ro` from `frontend/docker-compose.yml`. Two rebases
later, `sl-frontend-solo` had split that file's one openwebui service into **two** —
`openwebui` (`profiles: [gpu]`) and `openwebui-stock` (`profiles: [stock]`, the
fresh-clone deployment) — and the new service was written with the mount still on it.
The rebase replayed my deletion onto the service it was written against and left the new
one alone. Both sides behaved correctly; the result was a branch that deletes `config/`
and still binds it.

**Why nothing caught it for three attempts.** Every render this item ever ran named the
profiles the ANCHOR named — `--profile local` for inference, `--profile gpu --profile
tailscale` for the frontend. `openwebui-stock` is in neither, so it never appeared in a
rendered service list, and `openwebui-stock` has zero hits in this note or the test plan.
The citation sweep could not see it either: this is not a stale line NUMBER, it is a live
mount of a deleted directory.

**And it was not merely untidy.** `docker compose -f frontend/docker-compose.yml
--env-file .env.example --profile stock config` resolved the bind source to the repo's
`config/`. On a fresh clone — which is exactly who the `stock` profile is for — Docker
materialises a missing bind source as an empty directory, so the fresh-clone deployment
would have **recreated the directory this item's goal says is removed**. The same
empty-directory mechanism as F14, arriving from the opposite direction.

**The rule, and it is now an anchor criterion:** after every rebase, re-run the item's own
REMOVAL sweep against **every profile combination of every plane it touches**, not just
the combinations the anchor happened to name. A profile you never render is a service you
never check. The plan's T16 does this by construct and fails on any render naming
`config/`; it is cheap (one `docker compose config` per combination) and it is the only
thing that would have caught this.

**One combination legitimately refuses to render, and that refusal is the correct
result:** `--profile stock --profile gpu` together fails with `container name "openwebui"
is already in use`, because the two definitions deliberately share `container_name` —
`sl-frontend-solo` designed them mutually exclusive and `stack.manifest.toml:197` says so.
[source] Verified identical on `development`, so it is that item's design, not this
branch's breakage. A plan case must expect the refusal rather than score it a failure.
