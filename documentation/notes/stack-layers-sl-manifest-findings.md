# stack-layers / sl-manifest - findings

Sink for the `sl-manifest` harness item (developer pass, 2026-09-19, worktree
`wt-sl-manifest`, base `development` @ b28cbc5). These are true problems with
*other* things, turned up while encoding every plane's edges, keys and host
requirements into `stack.manifest.toml`. None of them is fixed here: this item
owns the manifest, `scripts/stack/stack.py`, its tests and its README, and
nothing else.

Each entry says what was checked and how, so the next person does not re-derive it.

---

## F1 - `ruff check .` is RED at the base commit, and so is the CI ruff job

**Checked:** `ruff check .` from the worktree root, `git status` clean for
`llm-queue/`, and the offending line at the base commit.

```text
E501 Line too long (103 > 100)
  --> llm-queue/src/llm_queue/__init__.py:9
```

The line is a docstring pointer that the plan-store migration rewrote to
`../documentation-plans-ai-stack/implementation-guide/LiteLLM-Proxy/DESIGN-B2-inference-queue.md`
(commit cfa7d4e, "documentation: move the plans to the plan store"). The longer
path pushed it past the **subproject's own** limit: `llm-queue/pyproject.toml:40-46`
sets `line-length = 100` and `select = ["E", "F", "I", "UP", "B", "ASYNC"]`,
where the root `ruff.toml` allows 120 and selects only `F` + `E9`.

`.github/workflows/ci.yml:47-54` runs `ruff check .` from the repo root, so the
`ruff` job is failing on `development` for a reason unrelated to any code change.

Verify pre-existence without touching the tree:

```text
git show b28cbc5:llm-queue/src/llm_queue/__init__.py | sed -n 9p | awk '{print length}'   # 103
```

`ruff check scripts/stack` (this item's files) is clean.

**Not fixed here:** it is another module's file and a one-line wrap belongs with
whoever owns the CI gate - `sl-closeout` is already touching the doc-move fallout.

---

## F2 - the anchor's compose project name comes from the checkout DIRECTORY name

**Checked:** `grep -n '^name:'` across all nine compose files.

Seven planes pin their project name (`name: inference`, `frontend`, `memory`,
`search`, `coder`, `open-brain`, `agent-org`). Two do not: the root
`docker-compose.yml` (the network anchor) and `portal/docker-compose.yml`.
Compose then derives the project name from the directory holding the compose
file. Portal is harmless - its directory *is* `portal`, and `portal-on.ps1`
passes `-p portal` anyway. The anchor is not: its project directory is the
checkout root, which is `ai-stack` only in the operator's checkout.

Consequence: `stack.ps1 up` (or `stack.py up`) run from a worktree such as
`.claude/worktrees/wt-sl-manifest` would create a **second, parallel** set of
networks named `wt-sl-manifest_llm-net` / `_app-net` / `_default`, while every
plane file asks for `ai-stack_llm-net` by name and would fail or attach to the
wrong seam. The `ai-stack_*` names are the contract every other project depends
on (root `docker-compose.yml:22-24` says so).

**Fix when someone owns that file:** add `name: ai-stack` to the root compose,
like every other plane already does. One line, render-identical in the
operator's checkout, and it makes the anchor checkout-location-independent -
which the cluster transition needs anyway.

**Meanwhile:** never run `up` from a worktree. This item only ever ran
`--dry-run` and `ps`.

---

## F3 - the GGUF model store is an absolute, operator-specific host path

`inference/docker-compose.yml:34`:

```yaml
- C:\Users\yamao\.lmstudio\models:/models:ro
```

Not a variable, not a default - a literal path on one Windows account. Any other
machine (the Linux OptiPlex, a fresh clone, CI) gets a bind mount of a directory
that does not exist. `LM_MODELS_DIR` appears nowhere in the file; the only
`LM_MODELS_*` names are the backup sidecar's schedule knobs (`:401-414`).

This corroborates the plan's assumption rather than contradicting it -
`sl-inference-split` is already scoped to introduce `LM_MODELS_DIR`. Recorded so
the item does not rediscover it, and so the manifest's `host` entry for
`inference` could name the real, current requirement.

---

## F4 - portal -> inference is a real cross-plane edge the audit's edge list did not carry

The brief's edge list gave portal two edges (frontend, and ob1 as optional).
There is a third:

- `config/caddy/Caddyfile:295` - `reverse_proxy llm-gateway-ui:8080`
- `inference/docker-compose.yml:316-327` - `llm-gateway-ui` joins `llm-net` **and
  `app-net`** with the comment "the portal Caddy fronts /ui at
  litellm.devinveller.ai behind Authelia", and explicitly carries no aliases so it
  can never be an inference path.

Encoded as `optional = ["ob1", "inference"]` on the portal plane: that vhost 502s
while the rest of the portal serves, so it is a soft edge, not a hard one.

Note for whoever writes the inventory generator (`sl-driver-parity`): three of
the portal's four upstreams are named **only in the Caddyfile**, not in any
compose file. A generator that reads compose files alone will not see them.

---

## F5 - coder -> inference is invisible to a compose-file-only reader

`coder/docker-compose.yml` never spells a `llama-cpp` URL. What it has is the
`llm-net` membership with a comment (`:28`, `:76`), and `llama-cpp` in the
`NO_PROXY` exemptions (`:47-48`). The actual base URL lives in the mounted
config:

- `little-coder/config/little-coder.config.yaml:11` - `base_url: http://llama-cpp:8080/v1`
- `little-coder/config/models.json:6` - `"baseUrl": "http://llama-cpp:8080/v1"`

The edge is real and hard (the daemon has no other model backend), but an
automated edge extractor restricted to compose files would miss it. Same class of
problem as F4.

---

## F6 - the "every plane file carries a `${VAR:?}` fail-loud guard" claim is not true of every plane

Root `docker-compose.yml:10-11` says the plane files are driven with the single
root `.env` and that "every plane file carries a `${VAR:?}` fail-loud guard
against running without it". Grepping `:?` across all nine files:

| Plane | guard |
|---|---|
| inference | `LITELLM_DB_PASSWORD` (`:301`) |
| frontend | `WEBUI_SECRET_KEY` (`:52`) |
| memory | `MCP_API_KEY` (`:41`) |
| search | `MULLVAD_WG_PRIVATE_KEY` (`:35`) |
| coder | `OPEN_TERMINAL_API_KEY` (`:40`) |
| ob1 | `OPS_GATEWAY_KEY` (`:252`) only - `MCP_ACCESS_KEY`, `POSTGRES_PASSWORD`, `OPENBRAIN_GATEWAY_KEY` are bare `${VAR}` and interpolate to empty |
| agent-org | **none** |
| portal | **none** - and it *is* driven with the root `.env` (`portal-on.ps1:46-49`) |

A bare `${VAR}` with the variable unset becomes an empty string and compose only
warns; the container starts with an empty password or an empty token. That is
the failure mode the guards exist to prevent, and it is exactly what the
manifest's `keys` list now catches *before* anything starts - but the compose
files themselves still do not fail loud for portal, agent-org and three of
ob1's four secrets.

---

## F7 - four variables OB1's compose requires are missing from its `.env.example`

`OB1/docker/docker-compose.yml` interpolates these with no default, and
`OB1/docker/.env.example` does not mention them (the live `OB1/docker/.env` has
all four, which is why nobody has noticed):

- `SURREAL_USER`
- `SURREAL_PASSWORD`
- `OB_APP_MEMORY_PASSWORD`
- `OPEN_NOTEBOOK_ENCRYPTION_KEY`

A fresh clone following `cp .env.example .env` brings up SurrealDB and Open
Notebook with empty credentials. Checked by comparing `^NAME=` in both files.

---

## F8 - there is no lease name for the anchor plane

`scripts/agent-harness/lease-names.conf` lists one name per compose plane, plus
coordination points - eight names, none of them the anchor. Bringing the shared
`ai-stack_*` networks up or down is shared-runtime work in exactly the sense the
lease file describes, but an agent doing it would have to pass `-AdHoc`.

The manifest therefore omits `lease` for the anchor rather than inventing a name
(inventing one would create the second lock for one fault domain that the config
file's own header warns about). Add `anchor` to `lease-names.conf` if the driver
ever starts driving the anchor from an agent session.

---

## F9 - the frontend reaches four other planes at boot, and its compose file shows one of them

Added 2026-09-19 after the tester (`wt-tester-manifest`) failed T1 on two missing
manifest edges; this is the general fact behind that miss, and it matters to
`sl-frontend-solo`, which has to make the frontend bring up standalone.

The `tailscale` companion shares openwebui's netns and `entrypoint.sh` raises one
tailnet **serve route per backend** at boot. The route table is
`entrypoint.sh:93-105`; every route is a `HOST=${VAR:-<service>}` plus an
`..._ENABLED` pair declared at `entrypoint.sh:54-77`, and **every toggle defaults
true**:

| Route | Host default | Plane | Declared in |
|---|---|---|---|
| `llama-cpp`, `llama-cpp-embed` | `llama-cpp`, `llama-cpp-embed` | inference | compose `:156,:159` + script `:54,:57` |
| `litellm-ui` | `llm-gateway-ui` | inference | **script only** `:70,:72` |
| `open-notebook`, `open-notebook-api` | `open_notebook` | ob1 | compose `:162,:164` + script `:60,:62` |
| `quartz` | `caddy` (compose) / `openbrain-wiki-viewer` (script fallback) | portal / ob1 | compose `:178,:180` + script `:66,:68` |
| `mattermost` | `mattermost` | agent-org | **script only** `:74,:76` |

Two consequences worth carrying forward:

1. **`frontend/docker-compose.yml` understates the coupling.** It passes the
   tailscale service exactly one variable (`:136-147` explains why the wildcard
   `env_file` was removed), so the `litellm-ui` and `mattermost` routes are
   invisible to anyone reading compose files - they live in `tailscale:local`.
   Same class as F5 (coder) and F4 (portal): three of the eight planes declare a
   real cross-plane dependency somewhere other than a compose file.
2. **A standalone frontend will try all five.** `sl-frontend-solo` puts tailscale
   behind a profile, which removes the problem for a fresh clone; while the
   profile is on, a default boot with nothing else running leaves five serve
   routes pointing at absent hosts (`entrypoint.sh`'s monitor loop retries them,
   which is why nobody has noticed).

**How the manifest missed two of them, so the next sweep does not.** The first
cut grepped for cross-plane hostnames with a word-boundary pattern whose
lookbehind excluded `-`. In `${OPEN_NOTEBOOK_HOST:-open_notebook}` the character
immediately before the hostname *is* the `-` of `:-`, so the `${VAR:-default}`
shape - the exact shape every one of these edges uses - was silently excluded by
the very sweep meant to find it. A hunt that finds nothing is not evidence until
you have checked it can find something: seed it with a known hit first.

---

## Not findings (checked, and fine)

- **agent-org -> coder is not a runtime edge.** The worker slices run
  `little-coder:local` / `little-coder-open-terminal:local`
  (`agent-org/docker/docker-compose.yml:294,351,392,438`), images the coder plane
  builds. Nothing in agent-org talks to a coder container. Encoded as a `host`
  requirement ("these images must exist"), not as a `requires` edge.
- **`PUBLIC_DOMAIN` in OB1's compose** appears only inside a comment
  (`OB1/docker/docker-compose.yml:808`); it is not interpolated and is not a
  missing variable.
- **agent-org -> ob1 is a latent integration, not an edge.**
  `agent-org/docker/docker-compose.yml:142` (`AO_OPENBRAIN_URL` ->
  `http://openbrain-mcp:8000`) and `:152` (`AO_RESEARCH_URL` ->
  `http://openbrain-research:8000`) name ob1-plane services, but `:141`
  `AO_OPENBRAIN_MIRROR_ENABLED` and `:151` `AO_GROUNDING_ENABLED` both default
  **false**, so a default boot never reaches for ob1 and nothing degrades when
  ob1 is down. Recorded in the manifest's agent-org comment as "considered, not
  an edge" with the line numbers, per the toggle rule in the manifest header.
- **ob1 -> agent-org goes through the HOST, not a docker network.**
  `OB1/docker/docker-compose.scheduled.yml:253` sends
  `openbrain-idea-refinery` to `http://host.docker.internal:8065` - out to the
  host and back in through agent-org's published loopback port. It is a real
  edge on this deployment (the `idea-refinery` profile is default-on and
  `IDEA_REFINERY_MM_TOKEN` is set in `OB1/docker/.env:13`), so the manifest
  carries it as `optional`, flagged as the one edge that does not cross a docker
  network. The cluster transition cannot move either plane without breaking it.
- **The digest is not a separate plane.**
  `OB1/docker/docker-compose.scheduled.yml` declares `name: open-brain` and is
  pulled in by `include:` at `OB1/docker/docker-compose.yml:15-16`, so it is part
  of the ob1 project and starts with it. Encoded as a *product* over
  `inference + search + ob1`, not a plane.
