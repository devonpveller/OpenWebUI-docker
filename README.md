# ai-stack

A self-hosted AI stack on Docker. **Open WebUI** is the chat front end; behind
it sit local **llama.cpp** inference behind a **LiteLLM** gateway with an
admission queue, a memory layer (**Open Brain**), a private
**search gateway** (SearXNG over a Mullvad WireGuard tunnel), a coding agent
(**little-coder**) with a governed multi-agent org (**agent-org**), and an
internet-facing **portal** (Caddy, Authelia, Cloudflare Tunnel). Each part is
its own Docker Compose project, and a small Python driver starts only the parts
you turn on. **A fresh clone runs Open WebUI and nothing else**; you add the
rest from the product menu.

## Hardware

Disk is what each product's images take. RAM is **usage observed on one
long-running reference deployment, not a minimum**. Everything runs on one
Docker host.

| Product | Disk for its images | RAM observed (reference deployment) | GPU |
|---|---|---|---|
| **chat** (the quickstart) | 5.1 GB | 1.0 GiB | none |
| **inference** | 9.7 GB, plus 17-22 GB per chat model and 0.6 GB for the embedding model | 13.9 GiB | NVIDIA: a 24 GB card for the chat models, plus by default a second card for embeddings (below) |
| **search** | 0.6 GB | 0.4 GiB | none |
| **open-brain** | 7.5 GB | 18.4 GiB | none of its own |
| **research** | 15.0 GB | 19.3 GiB + chat | none of its own |
| **coding-agent** | 7.7 GB | 3.2 GiB + chat | none of its own |
| **agent-org** | 3.9 GB | 4.2 GiB | none of its own |
| **digest** | 7.9 GB | 9.4 GiB | none of its own |
| **portal** | 5.6 GB | 0.2 GiB + chat | none |
| every plane, every profile | 27.2 GB, plus the models | about 35 GiB (agent-org's `cloud` lane not measured) | as inference |

How the figures were measured, so you can re-derive them:

- **Disk:** `docker image inspect --format '{{.Size}}'` (uncompressed, on an
  overlay2 store), summed once per image over what `docker compose config
  --images` lists for the product's planes and profiles. `:local` images are
  built on your machine. Data volumes and backups come on top. The
  every-plane figure was measured as 27.8 GB while the stack still had a
  memory plane (retired 2026-09-30); its two images, 0.58 GB, are taken off.
- **RAM:** `docker stats --no-stream`, summed over the same containers on the
  reference deployment - what it used, not what the product requires. Open
  Brain's wiki viewer (9.2 GiB) and database
  (4.0 GiB) dominate it and grow with your knowledge base. The chat figure is
  a fresh `stock` Open WebUI just after `/health` answered.
- **CPU:** no service reserves CPU; a few portal and Open Brain sidecars are
  capped at 0.1-1.5 CPUs. Every image is `linux/amd64`; nothing else is tested.
- **GPU:** two profiles reserve one (`driver: nvidia`, so the NVIDIA Container
  Toolkit is needed): inference's `local` - chat server on
  `GPU_LLAMA_CPP_DEVICE_ID`, default 0, embeddings on
  `GPU_LLAMA_CPP_EMBED_DEVICE_ID`, default 1
  ([`inference/compose/upstreams.yml`](inference/compose/upstreams.yml)) - and
  the frontend's `gpu`, a CUDA build of Open WebUI on `GPU_AISTACK_DEVICE_ID`,
  default 1 ([`frontend/docker-compose.yml`](frontend/docker-compose.yml)).
  Give ids the same index to share one card.
- **VRAM:** the chat server loads every layer onto the GPU (`--n-gpu-layers
  99`, `--no-mmap`) with a 262,144-token context and a `q4_0` KV cache
  ([`inference/config/llama-swap.config.yaml`](inference/config/llama-swap.config.yaml),
  `inference/.env.example`). Its two models, `Qwen3.6-27B-Q4_K_M.gguf` and
  `Qwen3.6-35B-A3B-Q4_K_M.gguf`, are 16.5 GB and 21.2 GB - the sizes Hugging
  Face lists under `lmstudio-community/Qwen3.6-27B-GGUF` and `-35B-A3B-GGUF`.
  Weights plus cache make **24 GB the floor for the shipped settings**; on a
  smaller card, use a smaller GGUF or lower `*_CTX_SIZE` in `inference/.env`.
  The embedding model, `bge-m3-f16.gguf`, is 0.63 GB.

**Without a GPU** everything but those two profiles runs, and chat, search and
the portal work fully. The inference plane is then a LiteLLM gateway with no
local model, so the products that call a model start but have nothing to
answer them; the gateway's optional cloud models do not change that as
shipped ([POSTURE.md](POSTURE.md) says why). The `inference` product turns
on `local`, so on a daemon with no NVIDIA runtime `up` refuses it before
anything starts, names the plane, profile and service, and prints
numbered steps that take the GPU profile back out (for inference: `disable
inference`, then `enable --plane inference` for the gateway alone).

## Quickstart

You need Docker Engine with the Compose plugin (`docker compose version`
works without `sudo`), git, curl, and **Python 3.11 or newer** (`python3
--version`; the driver uses only the standard library). No GPU.

**Linux:**

<!-- rehearsal:linux-quickstart - scripts/ci/linux-rehearsal.sh runs the next block as written -->
```sh
git clone --recurse-submodules https://github.com/devonpveller/OpenWebUI-docker.git ai-stack
cd ai-stack
cp frontend/.env.example frontend/.env
KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
sed -i.bak "s/^WEBUI_SECRET_KEY=.*/WEBUI_SECRET_KEY=$KEY/" frontend/.env && rm frontend/.env.bak
python3 scripts/stack/stack.py init
python3 scripts/stack/stack.py up
until curl -sf http://127.0.0.1:3000/health; do sleep 5; done; echo
```

**Windows** (PowerShell, Docker Desktop):

```powershell
git clone --recurse-submodules https://github.com/devonpveller/OpenWebUI-docker.git ai-stack
cd ai-stack
Copy-Item frontend/.env.example frontend/.env
$KEY = python -c "import secrets; print(secrets.token_hex(32))"
(Get-Content frontend/.env) -replace '^WEBUI_SECRET_KEY=.*', "WEBUI_SECRET_KEY=$KEY" | Set-Content -Encoding ascii frontend/.env
python scripts/stack/stack.py init
python scripts/stack/stack.py up
while ((curl.exe -s -o NUL -w '%{http_code}' http://127.0.0.1:3000/health) -ne '200') { Start-Sleep 5 }
```

The last line waits until Open WebUI answers `{"status":true}`; the first
start pulls a 5 GB image and can take several minutes. Then open
**http://127.0.0.1:3000** and create the first account, which becomes the
admin.

`WEBUI_SECRET_KEY` encrypts what Open WebUI stores, so keep it once set; the
driver refuses the shipped placeholder. `init` wrote `.stack/state.json`
(gitignored): your machine runs the `frontend` plane alone. `up` created the
shared `ai-stack_*` networks and started <!-- stack:count:frontend:stock -->**2** services with `stock`<!-- /stack:count:frontend:stock -->:
Open WebUI on its pinned upstream image and its backup sidecar.

## The product menu

A **product** is a slice of the stack you can turn on: the planes it needs, the
compose profiles it enables, and the surfaces you use it through. They are
declared in [`stack.manifest.toml`](stack.manifest.toml); the table is
generated from it. `python3 scripts/stack/stack.py list` prints a short form:
which planes are enabled on your machine, then each product's name and
description.

<!-- stack:product-menu -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Resolved from `stack.manifest.toml` by the same function `enable` uses. **Profiles** is what `enable <product>` writes per plane: the product's own, its surfaces', and each plane's `default = true` ones, closed over `requires`. `--headless` removes the Surfaces column. **Keys** are the manifest `keys` of every plane it starts; `enable` also refuses a value still equal to its `.env.example` placeholder._

| `enable <product>` | What it is | Starts (planes, in order) | Profiles it turns on | Surfaces (`--headless` drops these) | Keys it will ask for |
|---|---|---|---|---|---|
| **chat** | Open WebUI on its own - what a fresh clone gets | anchor, frontend | - | - | `WEBUI_SECRET_KEY` |
| **inference** | the LLM front door and the local llama.cpp backends | anchor, inference | inference: `local` | - | `LITELLM_DB_PASSWORD`, `LITELLM_MASTER_KEY` |
| **search** | the private search gateway (SearXNG behind Mullvad) | anchor, search | - | - | `MULLVAD_WG_PRIVATE_KEY`, `MULLVAD_WG_ADDRESSES` |
| **open-brain** | the Open Brain knowledge core | anchor, inference, search, ob1 | ob1: `idea-refinery`, `research`, `wiki` | ob1: `wiki` | `LITELLM_DB_PASSWORD`, `LITELLM_MASTER_KEY`, `MULLVAD_WG_PRIVATE_KEY`, `MULLVAD_WG_ADDRESSES`, `MCP_ACCESS_KEY`, `POSTGRES_PASSWORD`, `OPS_GATEWAY_KEY`, `OPENBRAIN_GATEWAY_KEY` |
| **research** | the research engine: OB1 + inference + search, read through OWUI, the wiki and Open Notebook | anchor, inference, frontend, search, ob1 | ob1: `idea-refinery`, `research`, `wiki`, `notebook` | ob1: `wiki`, `notebook` | `LITELLM_DB_PASSWORD`, `LITELLM_MASTER_KEY`, `WEBUI_SECRET_KEY`, `MULLVAD_WG_PRIVATE_KEY`, `MULLVAD_WG_ADDRESSES`, `MCP_ACCESS_KEY`, `POSTGRES_PASSWORD`, `OPS_GATEWAY_KEY`, `OPENBRAIN_GATEWAY_KEY` |
| **pantry** | the household pantry and meal planner: the OWUI Kitchen model + tool + the openbrain-pantry service (Open Brain's database holds the data) | anchor, inference, frontend, search, ob1 | ob1: `idea-refinery`, `research`, `pantry` | - | `LITELLM_DB_PASSWORD`, `LITELLM_MASTER_KEY`, `WEBUI_SECRET_KEY`, `MULLVAD_WG_PRIVATE_KEY`, `MULLVAD_WG_ADDRESSES`, `MCP_ACCESS_KEY`, `POSTGRES_PASSWORD`, `OPS_GATEWAY_KEY`, `OPENBRAIN_GATEWAY_KEY` |
| **coding-agent** | little-coder, the coding agent; its surface is Open WebUI | anchor, inference, frontend, coder | - | `frontend` (the whole plane) | `LITELLM_DB_PASSWORD`, `LITELLM_MASTER_KEY`, `WEBUI_SECRET_KEY`, `OPEN_TERMINAL_API_KEY` |
| **agent-org** | the governed multi-agent org; Mattermost is its INTERNAL surface, so --headless drops nothing | anchor, inference, agent-org | agent-org: `workers` | - | `LITELLM_DB_PASSWORD`, `LITELLM_MASTER_KEY`, `MM_DB_PASSWORD`, `AO_DB_PASSWORD` |
| **digest** | the scheduled digest chain inside OB1 (gmail pull -> research -> podcast -> digest) | anchor, inference, search, ob1 | ob1: `idea-refinery`, `research`, `notebook` | - | `LITELLM_DB_PASSWORD`, `LITELLM_MASTER_KEY`, `MULLVAD_WG_PRIVATE_KEY`, `MULLVAD_WG_ADDRESSES`, `MCP_ACCESS_KEY`, `POSTGRES_PASSWORD`, `OPS_GATEWAY_KEY`, `OPENBRAIN_GATEWAY_KEY` |
| **portal** | the internet front-end (started by hand: portal-on.ps1) | anchor, frontend, portal *(manual)* | portal: `internet` | - | `WEBUI_SECRET_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`, `AUTHELIA_JWT_SECRET`, `AUTHELIA_SESSION_SECRET`, `AUTHELIA_STORAGE_ENCRYPTION_KEY`, `PUBLIC_DOMAIN` |

<!-- /stack:product-menu -->

What each one gives you, and what it needs besides Docker:

| Product | Gives you | Also needs |
|---|---|---|
| **chat** | Open WebUI on `127.0.0.1:3000` | nothing |
| **inference** | the model endpoints every other plane calls, with the local llama.cpp models registered ([README](inference/README.md)) | the GPU; the chat GGUFs at the paths `inference/.env` names, under `data/models/gguf/` or its `LM_MODELS_DIR`; `bge-m3-f16.gguf` in `data/models/embeddings/`. Without a GPU, use `enable --plane inference` (the gateway alone) |
| **search** | private web search on `127.0.0.1:8085`, every query leaving through Mullvad ([README](search/README.md)) | a Mullvad WireGuard key and `/dev/net/tun` |
| **open-brain** | the knowledge base: capture, retrieval, a compiled wiki on `127.0.0.1:8812` | the OB1 submodule (the clone above fetched it) |
| **research** | open-brain plus the research engine, read in Open WebUI, the wiki and Open Notebook (`127.0.0.1:8503`) | as open-brain |
| **coding-agent** | little-coder, driven from Open WebUI ([README](coder/README.md)) | nothing more |
| **agent-org** | a governed team of agents, coordinated in Mattermost on `127.0.0.1:8065` ([README](agent-org/README.md)) | nothing more |
| **digest** | the scheduled mail-and-calendar digest and podcast chain | Google OAuth files under `OB1/secrets/google/` |
| **portal** | Open WebUI on the internet through a Cloudflare tunnel, behind Authelia ([README](portal/README.md)) | a tunnel token and a domain; started only by `scripts/portal/portal-on.ps1` (PowerShell), never by the driver |

Turning one on:

```sh
python3 scripts/stack/stack.py enable research    # a product: prints the planes and profiles it enabled
python3 scripts/stack/stack.py up                 # starts what is enabled, in dependency order
python3 scripts/stack/stack.py disable research   # takes out only what research added
```

`enable <name>` always means the product of that name, including the four that
are also plane names (`inference`, `search`, `agent-org`, `portal`);
`enable --plane <name>` acts on the plane alone. A product brings the planes
it requires (`coding-agent` brings inference) and turns on its
profiles - enabling `inference` registers the local models, with no `.env`
edit. `enable` refuses before it writes anything if a key is blank or still its
`.env.example` placeholder, naming the key and the file, so the loop is:
`enable`, copy that plane's `.env.example` to `.env`, fill in what it named,
`enable` again, `up`. `up` applies the same key check before it starts
anything, so a key blanked after `enable` is caught there too. `disable
<product>` removes a plane only when no other enabled product, direct
enable or requiring plane still holds it, and says which it kept and why.
`--headless` leaves out the reading surfaces and keeps the engines.

`up` starts the planes in order and stops at the first one that fails, so a
product that cannot come up blocks every plane after it, even an unrelated
one - search without a working Mullvad key, for example. Take it out before
you enable the next product: `python3 scripts/stack/stack.py disable search`,
then `python3 scripts/stack/stack.py down search` to remove the containers
the failed attempt left (an unhealthy leftover also fails `health`).

## How it is laid out

One Docker Compose project per plane, around a root
[`docker-compose.yml`](docker-compose.yml) that declares only the shared
networks. The counts are rendered from each plane's `.env.example`:

<!-- stack:plane-table -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Service counts are `docker compose config` renders with each plane's `.env.example` and `COMPOSE_PROFILES` cleared, under exactly the profiles named - "the driver's default" is what `up` passes before any `enable`._

| Plane (compose project) | Compose file | Services, by the profiles passed | Published host ports | Started by |
|---|---|---|---|---|
| **anchor** (`ai-stack`) | `docker-compose.yml` | 0 - it declares networks only | none | `up`, always (implicit) |
| **inference** (`inference`) | `inference/docker-compose.yml` | 4 with no profile; 8 with `local` | `127.0.0.1:8081`, `127.0.0.1:8082` | `up`, once enabled |
| **frontend** (`frontend`) | `frontend/docker-compose.yml` | 1 with no profile; 2 with `stock`; 2 with `gpu`; 4 with `gpu` + `tailscale`; every profile (`stock`, `gpu`, `tailscale`) does not render (compose refuses the combination) | `127.0.0.1:3000` | `up`, once enabled |
| **search** (`search`) | `search/docker-compose.yml` | 4 with no profile | `127.0.0.1:8085` | `up`, once enabled |
| **coder** (`coder`) | `coder/docker-compose.yml` | 4 with no profile | `127.0.0.1:9091` | `up`, once enabled |
| **ob1** (`open-brain`) | `OB1/docker/docker-compose.yml` | 20 with no profile; 23 with `idea-refinery` + `research` (the driver's default); 22 with `research`; 24 with `wiki`; 23 with `notebook`; 21 with `pantry`; 31 with every profile (`idea-refinery`, `research`, `wiki`, `notebook`, `pantry`) | `127.0.0.1:3001`, `127.0.0.1:5055`, `127.0.0.1:8003`, `127.0.0.1:8061`, `127.0.0.1:8062`, `127.0.0.1:8503`, `127.0.0.1:8810`, `127.0.0.1:8811`, `127.0.0.1:8812`, `127.0.0.1:8813`, `127.0.0.1:8814`, `127.0.0.1:8815`, `127.0.0.1:8816`, `127.0.0.1:8817`, `127.0.0.1:8818`, `127.0.0.1:8819` | `up`, once enabled |
| **agent-org** (`agent-org`) | `agent-org/docker/docker-compose.yml` | 6 with no profile; 13 with `workers`; 9 with `cloud`; 16 with every profile (`workers`, `cloud`) | `127.0.0.1:8065`, `127.0.0.1:8830` | `up`, once enabled |
| **portal** (`portal`) | `portal/docker-compose.yml` | 10 with no profile; 12 with `internet` | none | by hand: `scripts/portal/portal-on.ps1 / scripts/portal/portal-off.ps1` |

<!-- /stack:plane-table -->

- **Every plane owns its environment.** Compose reads `<plane>/.env` from the
  plane's own directory, so nothing passes `--env-file` and your working
  directory does not matter. Each ships a `<plane>/.env.example`; a plane
  refuses to render while its `.env` is missing. The root `.env` is only for
  host-wide scripts (the NAS mirror, for one); the quickstart does not need it.
- **Inference has one rule.** Every service reaches a model through
  `http://llama-cpp:8080` / `http://llama-cpp-embed:8080`, network aliases on
  the LiteLLM gateway, which forwards through `llm-queue` to the llama.cpp
  servers. Never route around LiteLLM; a pre-commit check enforces it.

| Path | What it is |
|---|---|
| [`stack.manifest.toml`](stack.manifest.toml) | The inventory: every plane's compose file, requirements, profiles, host needs and keys, and every product |
| [`scripts/stack/`](scripts/stack/README.md) | The driver, `stack.py`, and its full reference |
| `frontend/` `inference/` `search/` `coder/` `portal/` | The planes: compose file, `.env.example`, README, and their own source and config |
| `OB1/`, `agent-org/` | Open Brain (a pinned git submodule) and the multi-agent org |
| `openbrain-gateway/`, `little-coder/`, `backup/` | Source and backup scripts more than one plane uses |
| `scripts/` | Recovery, checks, backups, maintenance and the git-hook gates |
| [`documentation/runbooks/`](documentation/runbooks/) | Operating procedures: backups and restore, updates, incidents |

## Operating it

Every verb below is standard-library Python and runs on Linux, macOS and
Windows (spell it `python` on Windows). The full list, with every refusal, is in
[`scripts/stack/README.md`](scripts/stack/README.md).

```sh
python3 scripts/stack/stack.py status             # docker compose ps for each enabled plane
python3 scripts/stack/stack.py doctor             # docker, env files, blank or placeholder keys
python3 scripts/stack/stack.py health             # functional probes; exit code = number failed
python3 scripts/stack/stack.py up --dry-run       # print the docker commands, run nothing
python3 scripts/stack/stack.py restart frontend   # one plane in place
python3 scripts/stack/stack.py recover            # stop in reverse order, start in order, gate every container
python3 scripts/stack/stack.py backup frontend    # one verified tar.gz per volume, under backups/frontend/
python3 scripts/stack/stack.py restore frontend --from backups/frontend/manual-<stamp>
python3 scripts/stack/stack.py stats              # container CPU and memory, plus the inference queue
python3 scripts/stack/stack.py down               # stop what is enabled, in reverse order
```

`health` probes only the planes you enabled, plus the shared networks; the
full set is <!-- stack:health-count -->16 probes with every plane enabled, when the frontend deploys the `tailscale` profile, PowerShell is on PATH (`powershell` on Windows, `pwsh` elsewhere), Open WebUI has at least one plugin deployed and the ob1 `pantry` profile is enabled (13 when none of those holds)<!-- /stack:health-count -->.
`restore` checks every archive's sha256 and refuses while a container holds
the volume. Besides these manual backups, each stateful store has a backup
sidecar in its own plane writing to `backups/<service>/` on a schedule;
intervals, restore steps and the NAS mirror are in
[`documentation/runbooks/backup-restore-runbook.md`](documentation/runbooks/backup-restore-runbook.md).

Two rules protect a running stack: **never restart `openwebui` alone** under
the `tailscale` profile (tailscale shares its network namespace; restart
openwebui, wait until healthy, then tailscale - `recover` does), and **never
GET LiteLLM's bare `/health` through the alias** (it loads every model; probe
`/health/liveliness`). Windows-only, in PowerShell:
`scripts/recovery/emergency-recovery.ps1` (adds `nuclear` and `gpu-reset`), the
portal scripts, and the scheduled watchdog, NAS mirror and maintenance tasks.

## Contributing

<!-- rehearsal:contributing-hooks - scripts/ci/linux-rehearsal.sh runs the next block as written -->
```sh
git config core.hooksPath .githooks                           # the pre-commit gates
./.githooks/commit-msg /dev/null && echo "hooks can run"      # must print: hooks can run
```

The hooks block staged secrets, CRLF in shell scripts, inference routed around
LiteLLM, and stale generated docs, among others. Most gates are PowerShell (Windows
PowerShell or `pwsh`). With only Python and `sh`, nine still run: the secret,
line-ending, routing and doc-placement gates as Python twins, the generated-docs gate
(`docs-blocks`), the exec-bit drift gate and the personal-identifier gate in Python,
and the two file-mode gates in `sh`. The six that need PowerShell (corpus exposure,
project configs, env-file scope and the three OB1 gates) each print `SKIPPED
<gate>: needs PowerShell`, and the summary line names every gate that ran and
every one skipped ([`.githooks/README.md`](.githooks/README.md)). Never use `--no-verify`.
The personal-identifier gate (`scripts/checks/check_identity.py`) refuses a private or tailnet IP, a `*.ts.net` name, a user-profile or checkout-drive path or a personal email; it needs no setup (the optional local denylist is described in `.identity-denylist.example`), and what may stay is in `scripts/checks/identity-allowlist.txt` with a reason.

Before you push, run the checks CI's `ruff` and `stack-driver` jobs run
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)). Besides the
quickstart's prerequisites they need Python's `venv` module (Debian and Ubuntu:
`sudo apt install python3-venv`); the block installs `ruff` and `pytest` into a
virtual environment outside the checkout, so it works where the system Python
refuses `pip install` (PEP 668). They also need the OB1 submodule at its pinned
commit and an `.env` in every plane directory - CI copies the examples first,
and so should you (the loop never overwrites a real one; the checks render from
the examples, the files only have to exist):

<!-- rehearsal:contributing-checks - scripts/ci/linux-rehearsal.sh runs the next block as written -->
```sh
python3 -m venv ~/.venvs/ai-stack && . ~/.venvs/ai-stack/bin/activate
pip install ruff pytest
for p in . frontend inference search coder portal agent-org/docker OB1/docker; do [ -e "$p/.env" ] || cp "$p/.env.example" "$p/.env"; done
ruff check .
python3 -m pytest scripts/stack -q
python3 scripts/stack/stack.py inventory --check
python3 scripts/stack/stack.py docs --check
```

`docs --check` exits 0 when every generated block matches, 1 when one is stale,
3 when a block could not be compared at all (no docker, a missing `.env`, OB1
not checked out) and 4 when OB1 is checked out but not at its pinned commit or
has edits; 3 and 4 are not a pass. Without the `.env` files, `inventory --check`
fails and tells you to `--write` - don't; create them and re-run. One more
check runs locally only, not in CI: `python3 scripts/checks/check-md-links.py`,
the relative-link sweep.

Tables between `stack:` comment markers are generated: change the manifest or
a compose file, run `stack.py docs --write`, and never edit inside a block. A
container added, removed or moved touches the manifest, recovery, backups and
health probes too - follow
[`SERVICE-LIFECYCLE.md`](documentation/runbooks/SERVICE-LIFECYCLE.md). CI also
runs this quickstart on a clean daemon (`scripts/stack/rehearse-fresh-clone.sh`).

<a id="posture-local-first-cloud-capable"></a>

## Security and posture

Secrets live only in `.env` files and `secrets/` directories, both gitignored.
Every published port binds to `127.0.0.1`; the only ways in from outside are
the portal and the frontend's optional `tailscale` profile, and only once you
turn them on. `docker compose config` prints secrets in plain text, so grep
the part you need. Details: [SECURITY.md](SECURITY.md).

The stack is **local-first and cloud-capable**. No component sends a prompt, a
document or a memory to a model provider by default: model calls go to
llama.cpp on your machine through a LiteLLM gateway that sits only on internal
Docker networks, and the parts that can use the cloud ship switched off. That
is not "nothing reaches the internet" - a few containers do, starting with Open
WebUI's backup sidecar in the quickstart, and some services fetch URLs when you
use them. The full egress inventory, what turns each part on, and how to
re-derive it: [POSTURE.md](POSTURE.md).
