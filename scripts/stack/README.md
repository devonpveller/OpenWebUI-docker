# scripts/stack - the manifest-driven stack driver

`stack.py` reads two files and drives Docker Compose from them:

| File | Committed? | Says |
|---|---|---|
| `stack.manifest.toml` (repo root) | yes | every plane: compose file, what it requires, its profiles, what the host must provide, which keys must be set; and the products that group planes into vertical slices |
| `.stack/state.json` | **no** (gitignored) | which planes and profiles **this machine** enables, and which docker context each runs on |

The manifest is shared and declarative; the state is per host. The driver never
writes the manifest.

Standard library only, Python >= 3.11 (`tomllib`). A fresh host needs nothing
but Python and Docker. `scripts/stack/test_stack.py` enforces that rule with an
AST check, and the whole suite is hermetic - the docker call is injected, so no
test ever reaches a daemon.

`stack.ps1` **is a shim over this driver** since 2026-09-19 (`sl-driver-parity`).
`sl-ob1-profiles` had given that script's plane registry all four OB1 profiles to
keep every running container starting; the registry is gone, and it is NOT
expressed here as the same set - `idea-refinery` is `default` and `requires`
`research`, so `up` passes both, which against the pinned gitlink renders
<!-- stack:count:ob1:default -->**23** services with `idea-refinery` + `research` (the driver's default)<!-- /stack:count:ob1:default -->, against
<!-- stack:count:ob1:all -->**30** services with every profile (`idea-refinery`, `research`, `wiki`, `notebook`)<!-- /stack:count:ob1:all -->. `wiki` and `notebook`
come from `enable research` or `COMPOSE_PROFILES` in `OB1/docker/.env` (see
`[declared, not rendered]` below).
It forwards its arguments and exits with the driver's code; it holds no plane
registry, no probe and no ordering of its own, so there is nothing in it left to
drift. It survives because runbooks, plane READMEs and compose comments say
`.\scripts\stack\stack.ps1 up <plane>` in about forty places. Retiring it is a
later item.

Two things the shim has to translate, and both are in its own header:

- `stack.ps1 up` (its `$Plane` default is `all`) means **every declared plane**,
  which the driver spells `up --all`. A bare `stack.py up` means the narrower
  "whatever this machine enables" - on a fresh clone, the frontend alone.
- `stack.ps1 restart` with no plane is forwarded as `restart all`, so the
  refusal is the driver's rather than a second copy of it.

---

## Quick start

```text
python scripts/stack/stack.py list                # what exists, what is on
python scripts/stack/stack.py init                # write a state file (default: frontend)
python scripts/stack/stack.py enable research     # turn a product on
python scripts/stack/stack.py up --dry-run        # the exact docker lines, run nothing
python scripts/stack/stack.py up                  # start them, in dependency order
python scripts/stack/stack.py up --all            # every declared plane (what stack.ps1 up did)
python scripts/stack/stack.py up coder            # exactly one plane
python scripts/stack/stack.py doctor              # docker, env files, blank keys
python scripts/stack/stack.py health              # the probes of the enabled planes (read-only)
python scripts/stack/stack.py stats               # container CPU/mem/net + inference queue board and ledger
python scripts/stack/stack.py recover --dry-run   # the ordered, gated restart plan; stops nothing
python scripts/stack/stack.py recover             # stop in reverse order, start in order, gate every container
python scripts/stack/stack.py backup frontend     # backups/frontend/manual-<UTC>/ : one tar.gz per named volume
python scripts/stack/stack.py restore frontend --from backups/frontend/manual-<UTC>
python scripts/stack/stack.py inventory --check   # is stack-services.json still true?
python scripts/stack/stack.py docs --check        # are the generated blocks in the docs still true?
python scripts/stack/stack.py labels --dry-run    # which Open WebUI role names would change
python scripts/stack/stack.py labels              # set them from the model files (idempotent)
```

`status`, `health`, `doctor`, `stats`, `inventory --check` and `docs --check` are **read-only**:
they start, stop and recreate nothing, so they need no plane lease. `recover`,
`backup` and `restore` are not: hold the plane's lease
(`scripts/agent-harness/lease.ps1`) before running them against a shared host.

On Linux, spell it `python3`; every verb is standard-library Python and runs
there. `stack.ps1` and `scripts/recovery/emergency-recovery.ps1` stay as the
Windows extras they always were.

With **no state file at all** the machine runs `frontend` and nothing else -
that is what a fresh clone gets.

### Output and exit codes

Everything - refusals included - goes to **stdout**, and the exit code carries
the failure. That is deliberate: PowerShell 5.1 turns a native command's stderr
into a terminating `NativeCommandError` under `$ErrorActionPreference = 'Stop'`
(the trap that once swallowed nine probes in `stack.ps1 health`), so a driver
meant to be called from a `.ps1` must not write there.

| Code | Means |
|---|---|
| 0 | fine |
| 1 | refused, or a docker command exited non-zero |
| 2 | usage error (no verb) |

`health` is the deliberate exception and says so in the module docstring: its
exit code is **the number of failed probes**, which is what `stack.ps1 health`
has always returned and what remote uptime watching reads.

---

## The manifest

### Two graphs, deliberately not merged

| Key | Means | Used for |
|---|---|---|
| `requires` | what a plane needs to **run** | `up` ordering; `enable` refuses when one is off |
| `optional` | a soft edge - the plane runs without it, degraded | documentation only: never orders, never refuses |
| `surfaces` (on a product) | what a **person** needs to reach an engine | pulled in by `enable <product>` unless `--headless` |

Every `requires` and `optional` edge in the manifest carries its evidence as a
`<file>:<line>` citation in the comment above it - usually a compose file, but
for two planes the hostname lives in mounted config or in the image's
entrypoint, and the citation says so.

**Where the line is** for the common `HOST=${VAR:-<other plane's service>}` plus
`..._ENABLED=${VAR:-<bool>}` shape: **the toggle's default decides.** Default
true means a default boot reaches for the other plane and degrades without it -
an `optional` edge. Default false means a default boot never touches it - not an
edge, and the manifest records it in the plane's comment as "considered, not an
edge" with its line numbers, so a reader can tell *seen and rejected* from *not
seen*.

### Plane keys

| Key | Meaning |
|---|---|
| `compose` | compose file, repo-root-relative, forward slashes. Printed verbatim into the docker command. |
| `env_file` | the `--env-file` argument. **Absent means absent**: compose then loads the `.env` sitting in the compose file's own directory. Since `sl-env-split` (2026-09-19) NO plane declares one - every project directory holds its own `.env` (`frontend/.env`, `inference/.env`, ... `OB1/docker/.env`), so the driver passes no flag at all. The key is still read, so a plane whose env genuinely lives elsewhere could declare one. |
| `lease` | the `scripts/agent-harness/lease-names.conf` name for the plane. Absent = no canonical lease name (the anchor). |
| `requires` / `optional` | see above. |
| `implicit` | the plane is started whenever anything runs and never has to be enabled. Only the anchor. No refusal ever names it, and `enable` never writes it into the state file. |
| `networks_only` | the compose file declares networks and no service. Only the anchor. `up` never runs `docker compose up -d` on it (compose exits 1, "no service selected"); it renders the file with `config --no-interpolate --format json` and runs `docker network create` for each declared network that does not exist. An existing network is never altered: if it MATCHES the declaration (driver, internal, attachable, each declared driver_opt and label) it is left as is; if it DIFFERS - e.g. an `ai-stack_llm-net` that is not internal - `up` REFUSES before creating anything, and `doctor` reports it as a FAIL. `down` still runs `docker compose down`. |
| `manual` | present when the driver must **not** start or stop this plane; the value names what does. Only the portal: exposing the stack to the internet stays a human action, exactly as `stack.ps1`'s header says. |
| `host` | what the machine itself must provide, in prose (a GPU, a tunnel, model files). `doctor` prints these; nothing enforces them. |
| `host_paths` | paths OUTSIDE the checkout that the plane builds from, each `{ path, contains, why, remedy }` with `path` repo-root-relative and `contains` the names that must exist inside it (memory: `.git` and `Dockerfile`), so an empty directory or a plain file does not pass. Unlike `host` this is checked: while one is missing or incomplete, `doctor` FAILs the plane and `enable`/`init` refuse, naming `remedy` (the command that creates it, run from the repo root). Only memory declares one: `../mnemory`, its build context. |
| `keys` | variable names that must exist and be non-blank in the plane's env file. A blank one makes `enable` refuse and name the key. So does a value still EQUAL to the non-blank value the plane's `.env.example` ships for that key - for a required key that shipped value is a placeholder by construction - and that one `doctor` and `up` refuse too, before anything starts. **Keys NOT listed here are covered as well**: any value in a plane's `.env.example` that matches `stack.py`'s `PLACEHOLDER_PATTERN` (change-me, REPLACE_WITH, your-/putyour, `<...>`, an example.com domain or address, "placeholder") is refused while the plane's `.env` still holds it, provided a service the plane runs under its active profiles interpolates it (`${VAR}` in the `config --no-interpolate` render; a bulk `env_file:` does not count). So TAILSCALE_AUTH_KEY counts under `tailscale` and not under `stock`. |
| `ports` | published **host** ports -> what answers on them. |
| `profiles` | compose profiles, each a sub-table with a `description` and **exactly one** of the three flags below; optionally `requires` (other profiles of the plane it needs) and `stands_in_for` (profiles it replaces when the GPU refusal takes them out - frontend's `stock` for `gpu`). |

#### Every profile says whether a default `up` starts it

| Flag | Means | Today |
|---|---|---|
| `default = true` | the driver passes `--profile <name>` on every invocation | `ob1`'s `idea-refinery` - parity with what `stack.ps1` always passed |
| `opt_in = true` | a **deliberate choice** turns it on - a product that declares it, or something outside the driver - and the description says what | `inference`'s `local` (`enable inference`, the product, or `COMPOSE_PROFILES=local` in `inference/.env`), `agent-org`'s `workers`/`cloud` (the operator - `stack.ps1`'s header always said these were not managed), `portal`'s `internet` (`portal-on.ps1`) |
| `pending = true` | declared here, **not yet in the compose file**; a later item adds it. Enabling one is a no-op and the driver says so | `frontend`'s `gpu`/`tailscale` |

Declaring none of the three is **refused** by `inventory --check`. That gate
exists because the dangerous change is a compose file gaining a profile around
services that are *already running*: the driver would then stop passing what
starts them, `up` would quietly bring up a smaller stack, and nothing would say
so. Forcing the declaration turns that into a decision someone made on purpose.

#### `--profile` REPLACES `COMPOSE_PROFILES`; it does not add to it

Measured 2026-09-19, compose v5.3.0, with the inference plane's own env file
carrying `COMPOSE_PROFILES=local`:

```text
docker compose -f inference/docker-compose.yml config --services
  -> 8 services            (the `local` half is on)
... --profile idea-refinery config --services
  -> 4 services            (`local` silently dropped)
```

So the moment the driver passes **any** flag to a plane, it must also pass every
profile that plane's env file already enabled, or it starts a subset of what a
bare invocation would have started - with no error anywhere. `effective_profiles()`
does exactly that union, and passing no flag at all stays safe because compose
then reads `COMPOSE_PROFILES` itself. That is today's path for every plane except
`ob1`.

#### ...and `--profile` does not SET `COMPOSE_PROFILES` either

The flags decide which services start; a service that interpolates
`${COMPOSE_PROFILES}` still sees whatever the variable was. `llm-gateway` does
(`inference/compose/gateway.yml`), and its config assembler registers the `local`
model group only when `local` is in that variable. So enabling the inference product (which
writes `local`) followed by `up` used to pass `--profile local`, start the
upstreams, and hand the gateway `COMPOSE_PROFILES=""`: **zero** models registered.
With `COMPOSE_PROFILES=local` in `inference/.env` the same gateway registers five.

So whenever the driver passes any `--profile`, it also sets `COMPOSE_PROFILES`
in that compose process's environment to **the same list** - `compose_command()`
returns the argv with the override attached, and the two seams that execute a
command (`subprocess_runner`, `subprocess_capture`, plus the backup/restore
pipe) merge it over the inherited environment. The rules:

- The value is `effective_profiles()`: the state file's profiles, the plane's
  `default` ones, their `requires` closure, **unioned** with the plane's own
  env-file `COMPOSE_PROFILES`. The variable therefore never says less than the
  env file did, and never differs from the flags.
- It **replaces** a `COMPOSE_PROFILES` exported in the shell, exactly as the
  flags already did, so another plane's value (`gpu,tailscale`) cannot reach the
  gateway.
- With **no** flag nothing is set, and compose reads the variable itself (shell,
  then the plane's env file) - unchanged.
- It is environment, not argv, so every printed line (`--dry-run` included)
  shows it as a prefix: `COMPOSE_PROFILES=local docker compose -f
  inference/docker-compose.yml --profile local up -d`. That is sh/bash syntax;
  in PowerShell set `$env:COMPOSE_PROFILES` first. Copy a line WITHOUT its
  prefix and the gateway gets the env file's value, not the flags.

Every service that reads `COMPOSE_PROFILES` in any plane's render is that one
gateway; the check is repeated in the item's findings.

### Product keys

| Key | Meaning |
|---|---|
| `description` | one line, shown by `list`. |
| `planes` | the planes the product needs. Their `requires` closure comes too. |
| `profiles` | `{ plane = ["profile", ...] }` - profiles to enable inside a plane. |
| `surfaces` | `{ plane = ["profile", ...] }` - how a person reaches the engine. Dropped by `--headless`; a plane that appears **only** under `surfaces` is itself dropped by `--headless`. |

Five names (`inference`, `memory`, `search`, `agent-org`, `portal`) are both a
plane and a product. A bare name means the **product**: a newcomer copies
`enable <name>` from the product menu and must get what the menu promises -
`enable memory` brings inference, `enable inference` turns on `local`.
`--plane <name>` acts on the plane alone (and `--product <name>` is still
accepted). **Both `enable` and `disable`** print a `# note:` line whenever a
shared name is used, saying which reading they took - `disable` is the
destructive half of the pair, so it is the one where a silent reading would be
worse - and `enable` then lists every plane it wrote with its profiles. The set
is derived from the manifest; `test_stack.py` pins it to these five.

### Shared modules

`[modules.<name>]` declares a repo-root tree that more than one plane consumes,
so it is not any one plane's internals: `path` (repo-root-relative) and
`consumers` (`plane = "how it is consumed"`). The driver reads nothing from it;
`scripts/stack/test_stack.py` holds it to the compose files - the planes whose
compose files reference `../backup` must be exactly the declared consumers.
Today there is one: `backup`.

### Ordering

`up` is a topological sort of `requires`; **ties are broken by the order the
planes are declared in the manifest**. That tie-break is what makes the full set
come out in exactly the order `stack.ps1` uses:

```text
anchor, inference, frontend, memory, search, coder, ob1, agent-org
```

`down` is the exact reverse. Re-ordering the `[planes.*]` tables re-orders
`up` - do it deliberately. `optional` edges never affect ordering.

`up` starts the enabled planes **plus everything they require**, which is why
the anchor is started even though `list` shows it as not enabled: nothing put it
in the state file, and nothing needs to. `list` prints an `up would start:` line
so the two are never confused.

---

## The state file

`.stack/state.json`, gitignored, written by `enable` / `disable` / `init`:

```json
{
  "version": 1,
  "planes": {
    "frontend":  { "profiles": [], "context": null,
                   "owners": { "plane": [], "product:coding-agent": [] } },
    "inference": { "profiles": ["local"], "context": "optiplex-1",
                   "owners": { "product:inference": ["local"] } }
  },
  "products": { "coding-agent": { "headless": false }, "inference": { "headless": false } }
}
```

`profiles` is what every verb reads - the union of what the plane's `owners`
asked for. `owners` records WHO enabled the plane: `plane` for a direct enable
(`enable --plane`, `init --planes`, the no-state default) and `product:<name>`
for each product, each with the profiles it asked for. `products` lists the
products enabled here. Both exist so `disable <product>` can take out only what
that product added (see `disable`). **A file written before they existed** has
neither key: every plane in it loads as enabled directly, owning its current
profiles, and no product counts as enabled. Reading such a file never rewrites
it; the first `enable`/`disable`/`init` saves it with the new keys. In a file
that HAS `products`, an empty `owners` is real: a plane kept only because
another enabled plane requires it. It loads as unowned (not as a direct enable)
and goes when its last requirer goes.

`enable` prints each plane's profiles and labels any it did not turn on itself:
`local (already on: product memory)`, `(default)`, `(required by another
profile)` - so `enable --plane inference` after `enable memory` does not read
as though `--plane` turned `local` on.

`context` is the Docker context the plane runs on; when set, the command becomes
`docker --context optiplex-1 compose -f ...`. That is the data the
cluster-transition phase reads; this item does no remote logic beyond the prefix.

An unreadable state file is refused, not ignored - a silently-defaulted state
would start the wrong set.

---

## Verbs, with their refusal cases

### `list`

Planes with `enabled` / `disabled`, the profiles and context of the enabled
ones, the `up would start:` line, and the products (`[enabled]` on each one the
state file records). Read-only, no docker.

### `status` / `up` / `down` - which planes?

All three take the same selection:

| Form | Acts on |
|---|---|
| *(nothing)* | the planes this machine **enables**. `up`/`down` add their `requires` closure; `status` does not - reporting on a plane nobody enabled is noise |
| `<plane>` | exactly that plane. `up <plane>` first ensures the anchor's networks, as a bare `up` does (it creates a missing one and starts nothing else), and prints a `#` note naming any other requirement it is **not** starting |
| `--all` | every plane the manifest declares except the `manual` ones - what a bare `stack.ps1 up` meant |

A plane name together with `--all` is refused.

### `status`

`docker compose ... ps` per selected plane, in dependency order. Read-only:
no lease needed, nothing is started, stopped or recreated. Exits 1 if any `ps`
exits non-zero.

### `up` / `down` [`--dry-run`]

`up` starts the selected planes in dependency order; `down` stops them in
reverse. `--dry-run` prints the exact
`[COMPOSE_PROFILES=p,...] docker [--context X] compose -f <file> [--profile p]... <verb>`
lines (the prefix whenever a profile is passed; see above) and runs **nothing**.

A `manual` plane (the portal) is never started or stopped; a `#` comment line
after the commands names the script that drives it.

If a docker command exits non-zero the run stops there and reports which plane
and which code - the rest is not attempted.

**Refuses**, before anything starts (an empty enabled set just prints a `#` note):

- a pinned submodule that is not initialised, or a manifest `keys` entry that is
  **blank, missing or still its shipped placeholder** (the same rule `enable`
  applies), or another key a running service reads that is still its shipped
  placeholder (`_preflight`; also under `--dry-run`);
- **a plane compose cannot render** (`up` and `recover`, not under `--dry-run`),
  checked on a GPU-less daemon because the GPU check below reads the render. It
  is its own refusal, headed `refused: compose cannot render <file>`, carrying
  compose's own error line, the command, and the plane's env file - never
  reported as a GPU problem and with no GPU steps (a host with a GPU gets the same
  error from compose's `up`). Fix what compose names and re-run;
- **a GPU the daemon does not have** (`_gpu_preflight`, `up` and `recover`, not
  under `--dry-run`, which reads nothing from docker). The driver asks `docker
  info` once per docker context; an `nvidia` runtime or an `nvidia.com/gpu` CDI
  device passes and nothing else is read. Otherwise it renders each selected
  plane - interpolated, under exactly the profiles `up` will run (flags, the
  plane's env-file `COMPOSE_PROFILES`, or the shell's, as compose would resolve
  them) - and refuses when a service in that render reserves an NVIDIA device
  (`deploy.resources.reservations.devices` with `driver: nvidia` or a `gpu`
  capability, `runtime: nvidia`, `gpus:`), naming each plane, profile and
  service, then numbered steps. The steps are **built from the state's owners and
  applied to a copy of the state as they are chosen**, so carrying them out takes
  the GPU profiles out and the refused command, re-run, gets past THIS refusal
  (any other check it then meets - a key, a render - speaks for itself):
  `disable <product>` for each product that asked for the profile (after `enable
  memory`: `disable memory`); `disable --plane <plane>` only when nothing but a
  direct enable holds it; an edit of `.stack/state.json` when a direct enable
  carries it and something else still needs the plane (a pre-products state
  file); `enable --plane <plane>` when the plane went with its owners (for
  inference: the gateway without its local backends); for the plane's env-file
  `COMPOSE_PROFILES`, the exact new value - the GPU profile dropped **together
  with every profile whose manifest `requires` reaches it**, and every profile
  that declares `stands_in_for` it added (frontend `gpu,tailscale` becomes
  `stock`, so Open WebUI still runs); `unset COMPOSE_PROFILES` when only the
  shell turns it on, followed - when the plane's env file would not then run
  the stand-in (no `COMPOSE_PROFILES` line, say) - by the env-file value that
  does, so both paths end at the same profiles; then the refused command **as typed** (`recover inference`,
  `up --all`). Each step names the interpreter that is running the driver
  (`python3 scripts/stack/stack.py ...` when it was started as `python3`), so a
  host with no `python` on PATH can follow it. `--plane` is never offered while a product owns the plane (it
  would be refused). Before this, a GPU-less host got compose's raw `could not
  select device driver "nvidia"` halfway through `up`, after the anchor and
  earlier planes had started. An unknown answer from `docker info` (docker not
  reachable, unparsable output) is not a refusal - compose then says why.

### `restart <plane>` [`--dry-run`]

**Refuses** `restart all` (naming `recover`, `down` + `up`, and
`scripts/recovery/emergency-recovery.ps1`, the Windows original of `recover`).
**Refuses** a `manual` plane, naming its script. Restarting a plane that is
not enabled prints a note and proceeds.

### `enable <product|plane>` [`--headless`] [`--plane`|`--product`]

A name that is both is the **product** (see *Product keys*); `--plane <name>`
takes the plane.

A **plane**: enables just that plane (plus its `default` profiles).

- **Refuses** when a required plane is not enabled, naming it *and* the command
  that would enable it - `--plane` when a product shares the name, since the
  refusal asked for the plane and not for the product's profiles:
  `refused: memory requires inference, which is not enabled (python scripts/stack/stack.py enable --plane inference)`
- **Refuses** when one of the plane's `keys` is blank or missing in the env file
  that plane reads, naming the key, whether it is blank or missing, the file, and
  the remedy:

  ```text
  refused: search needs these keys before it can be enabled:
    MULLVAD_WG_PRIVATE_KEY is blank in .env
  Set them in .env, then re-run (`python scripts/stack/stack.py doctor` lists every blank key on this machine).
  ```


A **product**: enables its planes, their `requires` closure, its `profiles`,
and its `surfaces` unless `--headless`, marking each plane `product:<name>` in
the state file's `owners` and the product in `products` (what `disable` reads).
`memory` declares `inference = ["local"]`: mnemory's `LLM_MODEL` and
`EMBED_MODEL` are registered at the gateway only under `local`.

- A product does **not** refuse on its own members being off - expanding them is
  the point. It still **refuses** on any member plane's blank key, naming the
  key *and* which plane reads it.
- `--headless` drops the surface profiles, and drops any plane that is only a
  surface (`coding-agent`'s frontend). A plane listed under both `planes` and
  `surfaces` stays (`research`'s frontend).

Enabling a `pending` profile is allowed and prints a note saying it changes
nothing until the item that adds it to the compose file lands.

### `disable <product|plane>` [`--plane`|`--product`]

A shared name is the product here too; `--plane <name>` disables the plane alone.

A **product** takes out only what it added. Its `product:<name>` mark comes off
every plane it enabled, and then a plane is removed only when **no owner is
left** (no other enabled product, no direct enable) **and no remaining plane
requires it**; a plane that stays loses only the profiles no remaining owner
asked for. It prints the planes removed, the planes kept and why (`product
coding-agent`, `enabled directly`, `required by memory`), and the profiles
dropped. A product that is **not enabled here** - including every product on a
state file written before products were tracked - is a no-op note, and nothing
is written. Measured on the attempt this replaced: `disable portal` with only
the frontend enabled removed the frontend (Open WebUI), because `portal`'s
product lists `frontend`.

A **plane** (`--plane`) is removed on its own. **Refuses** while a product
enabled it (naming the product - disable that instead), and while an enabled
plane still requires it (naming the dependents). Disabling a plane that is not
enabled is a no-op note.

### `doctor`

Reports docker on PATH, `docker compose version`, the Python version, the
manifest and state paths, and then per enabled plane: the compose file exists,
the env file exists (and how it is loaded), every blank, missing or
still-placeholder key, and the plane's `host` requirements. A compose file
missing because its submodule is not initialised names `git submodule update
--init <path>`. Exits 1 if anything is `[FAIL]`. Read-only.

### `health`

<!-- stack:health-probes -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Read out of `HealthSweep.run()` itself, with every host answer stubbed; `<...>` is what the live run fills in._

| Plane | Probe | Runs when |
|---|---|---|
| anchor | `0 unhealthy containers (found: <names>)` | always (the anchor is implicit) |
| anchor | `anchor: ai-stack_llm-net exists and is internal` | always (the anchor is implicit) |
| inference | `inference: llm-gateway liveliness` | the plane is enabled |
| inference | `inference: serving depth: <what it found>` | the plane is enabled |
| frontend | `frontend: OWUI http://127.0.0.1:3000/health` | the plane is enabled |
| frontend | `frontend: 8 tailnet serve routes` | the plane is enabled, and the frontend deploys the `tailscale` profile |
| frontend | `frontend: owui/ manifest rows drifted from live webui.db: <count>` | the plane is enabled, and PowerShell is on PATH (`powershell` on Windows, `pwsh` elsewhere) and Open WebUI has at least one plugin deployed |
| memory | `memory: cloud door http://127.0.0.1:8060/health` | the plane is enabled |
| search | `search: gateway http://127.0.0.1:8085/healthz` | the plane is enabled |
| search | `search: <verdict> - <n> engine(s) answering` | the plane is enabled |
| coder | `coder: little-coder daemon :8090/health` | the plane is enabled |
| ob1 | `OB1: open_notebook API :5055/api/config` | the plane is enabled |
| ob1 | `OB1: ops door :8062/health` | the plane is enabled |
| ob1 | `OB1: research-curator http://127.0.0.1:8816/health` | the plane is enabled |
| ob1 | `OB1: openbrain-db accepting connections` | the plane is enabled |
| agent-org | `agent-org: mattermost ping` | the plane is enabled |

**16 probes with every plane enabled, when the frontend deploys the `tailscale` profile, PowerShell is on PATH (`powershell` on Windows, `pwsh` elsewhere) and Open WebUI has at least one plugin deployed (14 when none of those holds).** Only the planes this machine enables are probed, plus the anchor; the exit code is the number of probes that failed.

<!-- /stack:health-probes -->

These are the probes `stack.ps1 health` ran, one for one, with the same pass
conditions, the same `[OK]` / `[FAIL]` line shape and the same exit code - **the
number of failed probes** - plus the serving-depth probe, which has no `.ps1`
ancestor. The table above is generated from `HealthSweep.run()`, so it cannot
list a probe the sweep does not run.

**Only the planes this machine enables are probed** (ac-front-door), plus the
implicit anchor - not the wider requires-closure `up` starts (on any state the
driver wrote the two are equal; on a hand-edited one they are not, and a plane
nobody enabled is not probed). The anchor probe checks that `ai-stack_llm-net`
exists AND is internal. A fresh clone runs the frontend's and the
anchor's probes and names the other six planes on one `[skip] not enabled on
this machine` line; the exit code counts failures among the probes that ran.
The full set - <!-- stack:health-count -->16 probes with every plane enabled, when the frontend deploys the `tailscale` profile, PowerShell is on PATH (`powershell` on Windows, `pwsh` elsewhere) and Open WebUI has at least one plugin deployed (14 when none of those holds)<!-- /stack:health-count --> - runs only when every plane is enabled. The owui-drift probe needs
PowerShell (`powershell` on Windows, `pwsh` elsewhere) and prints a `[skip]`
line where there is none.

#### The serving-depth probe: `inference: serving depth`

Added by `sl-recovery-backups` (2026-09-21) because **every other probe
was green for thirty hours while every chat returned
`500 upstream command exited prematurely`** (2026-09-19 18:54 -> 09-21 00:57).
`llama-cpp-upstream` had been recreated with `LM_MODELS_DIR` unset, so compose
bound its default `../../data/models/gguf` - an empty directory - at `/models`.
The container was healthy, the anchor network existed, LiteLLM's
`/health/liveliness` answered 200, and llama-swap's `/health` answers **without
loading a model**. Nothing asked whether inference could actually serve.

It runs whenever `local` is on for inference from ANY source - the state file,
`COMPOSE_PROFILES=local` in `inference/.env`, or the shell - or a
`llama-cpp-upstream` container exists at all (running or not; an unreadable
container list counts as existing). A shell `COMPOSE_PROFILES` exported for
another plane (frontend's `gpu,tailscale`) cannot switch it off. Only when none
of those holds is there no upstream by design - the gateway alone is the
documented GPU-less deployment, and the GPU refusal's steps lead there - and the
line reads ``serving depth: not applicable - inference runs without `local` ...``
and passes. It stays one line either way, so the probe count above holds. When the probe
runs and cannot read the upstream, its hint follows WHY it ran: `local` on (`up`
starts the upstream), a leftover container with `local` off (remove it with the
`rm -sf` line from `inference/README.md`, or turn `local` on), or an unreadable
`docker ps -a` (fix docker access).

What it checks, in the cheapest order that cannot be fooled:

1. **`.gguf` census** - `find /models` inside the upstream. **Zero FAILS**, and
   the line names the HOST path of the bind (`docker inspect`), because the host
   path is what an operator edits. Checked FIRST so an empty store yields a
   diagnosis instead of a 500.
2. **`/running`** - llama-swap's own list. A model in state `ready` PASSES and
   the line names it. Zero cost, and the normal case.
3. **One completion**, only when nothing is resident: `max_tokens` 3, THROUGH the
   gateway (never around it), 600 s timeout because a cold load is minutes -
   257 s measured on this host. 200 with a choice PASSES; anything else FAILS
   with the gateway's own sentence. It asks for the **role** `local-small`
   (`PROBE_ROLE`; model-roles, 2026-09-28), never "the first id that is not an
   embedding": the assembler loads `cloud.openrouter.yaml` before `local.yaml`,
   so with a cloud key set that rule picked `cloud-large` - which has no egress -
   and said nothing about the local backend. A gateway that does not list
   `local-small` FAILS naming it.

**What fails it:** an empty or missing `/models`; a completion that does not
return 200; `llama-cpp-upstream` not running; or `LITELLM_MASTER_KEY` missing
from `inference/.env`, which is reported as a named refusal rather than left to
surface as a 401. Steps 1-2 read `*-upstream` directly, which `CLAUDE.md` permits
for health/GPU/recovery probes; step 3 goes through the gateway like any caller.
The key value is never passed as an argument or logged - the request is made by a
script running inside `llm-gateway` that reads the container's own environment.

#### What the sweep touches

Read-only, and this is everything (counts measured 2026-09-21 on a healthy host):

* `docker ps` - once for unhealthy containers, and a second time with
  `--filter name=tailscale` ONLY when the frontend render has no `tailscale`
  service (the deployment guard's fallback, so one call on this host);
* `docker network inspect ai-stack_llm-net`;
* **one `docker compose -f frontend/docker-compose.yml config --services`** -
  the tailnet guard asking whether the `tailscale` profile is
  part of this deployment (added with `sl-frontend-solo`; it was missing from
  this list until a tester counted);
* **seven** read-only `docker exec`s - `llm-gateway`, `tailscale`,
  `little-coder`, `openbrain-db`, `agent-bridge`, and **two on
  `llama-cpp-upstream`** (the `.gguf` census and `/running`);
* conditionally, and only on the serving-depth probe's failure or cold paths:
  one `docker inspect llama-cpp-upstream` (to name the bind) or one further
  `docker exec llm-gateway` (the landing completion);
* one `powershell -File check-owui-drift.ps1 -CountOnly`;
* **seven** HTTP GETs - `:3000/health`, `:8060/health`, `:8085/healthz`,
  `:8085/health`, `:5055/api/config`, `:8062/health`, `:8816/health`. Seven, not
  six, because `search` gets two of them; that is the whole point of the third
  rule below.

**Why that render uses `.env` and not `.env.example`:** it is asking what THIS
host deploys, so it must read this host's `COMPOSE_PROFILES`. The inventory
generator asks the opposite question - what does the compose file DECLARE,
identically on every machine - and renders with `.env.example` for exactly that
reason. Same command, two questions; see `render_env_path`.

Rules the probes encode, each bought with an outage:

- **A failing probe never stops the sweep.** The first version of the
  owui-drift probe assigned outside a `Probe` block, so a stopped `openwebui`
  turned the check's stderr into a terminating `NativeCommandError`: five probe
  lines, no summary, and the eight later probes never ran. A stopped plane costs
  its own probe lines and nothing else.
- **`REFUSED` reads as FAIL.** `check-owui-drift.ps1 -CountOnly` prints a count,
  or the word `REFUSED` with a sentence on stderr. Anything that is not a number
  fails, and the reason goes in the probe's label.
- **`search` gets two probes.** `/healthz` answered 200 throughout the
  2026-09-11 outage - bing returned ten results for each query's first word,
  HTTP 200, no error. `/health` reports which engines actually put results into
  recent payloads; `unknown` (nothing searched yet) is not a failure, a measured
  `DEGRADED` is.
- **Never GET LiteLLM's bare `/health` through the alias** - it makes the
  gateway load every model it advertises. `/health/liveliness` only.

The probe NAMES are pinned in `test_stack.py` (`PS1_PROBES`), so a probe that is
dropped, merged into a neighbour or renamed fails the suite.

### `recover` [`<plane>`|`--all`] [`--dry-run`] [`--timeout SECONDS`]

The portable equivalent of `scripts/recovery/emergency-recovery.ps1 -Action
recover` - its FULL path (`Invoke-EmergencyRecovery`: stop everything in reverse
order, restart in dependency order, wait for health). Selection is the same as
`up`: the enabled planes plus their `requires` closure, one plane, or `--all`.

0. **The GPU check** `up` runs (see `up`), before anything stops or starts; not
   under `--dry-run`.
1. **Every plane is rendered first** (`docker compose -f <file> [--profile ...]
   config --format json`, with exactly the profiles `up` passes). A plane that
   cannot be rendered is a refusal while the stack is still running.
   Then every `depends_on` condition is checked, because the `up -d --no-deps`
   below skips compose's own checks: `service_healthy` on a service with no
   healthcheck (none in the compose file and none in its image, asked with a
   read-only `docker image inspect`) and `service_completed_successfully` on a
   service with `restart: always` / `unless-stopped` are **refused, naming the
   plane, the service and the target, before anything is stopped** - in
   `--dry-run` too. A healthcheck block with timing fields and no `test` is not
   a healthcheck of its own (compose merges it onto the image's), so the image is
   asked. An image that is not on the daemon cannot be asked before the stop;
   that is a `# WARNING` saying it is **decided after the pull**: once the
   target's level is up, its container is read and, if it has no health status at
   all, recover refuses by name before starting the dependant - where compose's
   own `up` refuses too.
2. **Stop**, planes in reverse of `up`'s order; inside a plane, its services in
   reverse depends_on levels (`docker compose ... stop --timeout 30 <services>`).
3. **Start**: the anchor's networks are ensured first (always, even for one
   plane - a recovery on a daemon that lost them would otherwise fail at the
   first `up`); then planes in `up`'s order, each plane's services level by
   level (`up -d --no-deps <services>`), and **every container is gated**. The
   gate's KIND is derived from the render and printed in `--dry-run` as
   `gate [<kind>]`:

   | kind | when | passes | fails |
   |---|---|---|---|
   | `completes` | another service depends_on it with `condition: service_completed_successfully` | it EXITS 0 - its dependants start only after that | any other exit code, or still not exited at the budget |
   | `healthy` | a compose healthcheck | `healthy` | `unhealthy` (at once), `exited`, `restarting` |
   | `one-shot` | `restart: "no"`, or no restart policy | exit 0 at any point; or running, unrestarted, for the settle window | a non-zero exit, a restart |
   | `settle` | a restart policy and no healthcheck | running with the same `RestartCount` and `StartedAt` for 15 s (or the declared `deploy.restart_policy.delay` plus one poll) | a restart, an exit or `restarting` inside the window (`restart loop: ...`, `exited with exit code N ... inside the settle window`) |

   A container that crashes only AFTER its window, or turns unhealthy after it
   was healthy, still passes - the gates cannot see that. The gates of one
   level run one after another, so each settle window adds its 15 s. The budget
   is the healthcheck's own worst case - `start_period + retries x (interval +
   timeout) + interval + 30 s` - or 300 s when the compose file declares none;
   `--timeout` sets one budget for all.
4. **The first failed gate stops the run**: `refused: recover stopped at
   <plane>: <service> (<container>) <what docker said>`, with the last
   healthcheck output and the planes left stopped. Exit 1.

The orders are **derived, not listed**. Container levels come from each
service's `depends_on` plus `network_mode: service:X`, so:

- **the netns rule** - `tailscale` runs in `openwebui`'s network namespace
  (`frontend/docker-compose.yml`): it stops before `openwebui` and starts after
  it is healthy, and `check_netns()` refuses any plan that would restart a
  namespace provider without its tenants after it. emergency-recovery.ps1
  writes the same rule by hand in `Invoke-MinimalRecovery` and relies on the
  project's depends_on in its full path;
- **the inference rule** - both llama.cpp upstreams and `llm-queue` start
  before `llm-gateway`, from `inference/compose/gateway.yml`'s depends_on.

`--dry-run` prints the whole plan - every `stop` and `up` line and every gate
with its budget - after reading the renders (read-only), and runs nothing else.

A key still at its shipped placeholder is **printed as a WARNING, not
refused**: recover brings back a deployment already running on those values,
and a refusal would leave a crashed host down until the key is rotated. `up`,
`enable` and `doctor` still refuse it.

**Deliberately not ported:** the diagnostics that choose the "minimal" path,
the fall-through to `nuclear`, the .ps1's GPU health check (`nvidia-smi`; the
driver's own GPU-availability check does run, as step 0), the tailscale `ping 8.8.8.8`, and
the `nuclear` / `gpu-reset` modes. **Differs from the .ps1 on purpose:** planes
go in `up`'s order, so inference starts before the frontend; the .ps1's full
`recover` path starts the frontend first, while its own minimal path and
`nuclear` start inference first. And **every gate is fatal**: the .ps1 throws
only when openwebui misses its gate and logs a WARN for every other one, then
starts the next plane anyway. `recover` stops at the first container that
fails, because starting a plane on top of a dependency that is not healthy is
how a partial outage becomes a silent one. It also has no fixed pauses (the
.ps1 sleeps 15 s and 20 s between phases) - the gates are the waits - and it
stops every container with `--timeout 30` (the .ps1 gives the frontend 45 s).

### `backup <plane>` [`--dest DIR`]

One `<volume>.tar.gz` per **named volume the plane's services mount under the
profiles it runs with**, read from the render - never a hand list. Written to
`backups/<plane>/manual-<UTC stamp>/` (or `DIR/<plane>/manual-<stamp>/`), with
`manifest.json` (volume, compose key, archive, bytes, sha256) and a
`SHA256SUMS` that `sha256sum -c` reads. The archive is streamed out of a
throwaway `alpine:3.21` helper (`--network none`, the volume mounted
read-only) - no bind mount, so the same verb works on Windows, on Linux, in a
DinD and over a docker context.

- The dump sidecar is found from the render, not from `depends_on` alone: a
  service in the same plane named `*backup*`/`*dump*` (or with such an image)
  that depends_on the engine, names it as a host in its environment (`PGHOST`,
  a URL, a DSN), or mounts the database volume.
- A **live copy** (a volume a running container holds) is recorded in
  `manifest.json` under `live_copy`, with the running containers and a warning,
  not only on the console: a SQLite file such as `webui.db` may be mid-write.
- A failed helper is removed by name (`docker rm -f`) before `backup` or
  `restore` returns, and the output says whether it is gone.
- A **database data directory whose engine is running** (postgres, pgvector,
  surrealdb) is **not tarred**: it is named, with the plane's dump sidecar, as
  "use the plane's dump for a consistent copy". With the engine stopped it is
  archived as a cold copy.
- A volume absent from the daemon is skipped by name; so is an external one.
- **Host bind mounts are not archived**; the output counts the writable ones.
- Nothing archived is a refusal (exit 1), and the empty directory is removed.

### `restore <plane> --from <dir|manifest.json|archive>` [`--volume NAME`]

Every check runs **before anything changes**, and each failure is a refusal
that changed nothing: the manifest is `backup`'s and names this plane; every
selected archive's **sha256 matches**; every selected volume is one the
plane's render declares; and **no running container holds it** (asked of the
daemon, so a container from any project counts). Only then are the selected
volumes touched: a missing one is created with compose's own
`com.docker.compose.project` / `.volume` labels (so the next `up` adopts it),
and its contents are **replaced** by the archive's - extracted into a staging
directory first, so a torn archive leaves the old contents as they were.
That staging needs **free space for the old contents and the new at once**
(about 10 GB extra for this host's Open WebUI volume); a restore that runs out
fails in the extraction and leaves the old contents untouched.
`--volume` takes the volume name or its compose key; `--from` a single
`.tar.gz` implies it.

### `stats` [`--hours N`] [`--bucket-minutes N`]

Off Windows: `docker stats --no-stream` for every running container of every
enabled plane, then - when the inference plane is enabled - the llm-queue
`/observe/queue` board and the LiteLLM spend ledger (demand buckets, by caller,
global totals), the same two sources `stack-stats.ps1` reads. With inference
**not enabled** it says so instead of printing zeros; an unreachable ledger is
named and exits 1. A container that disappears between `compose ps` and `docker
stats` costs only its own row, which is marked `(gone: ...)`. On Windows it still hands off to
`scripts/stack/stack-stats.ps1`, unchanged.

### `inventory --write` | `--check`

Generates `scripts/lib/stack-services.json`, the inventory
`scripts/recovery/status_check.py` and `scripts/checks/stack-watchdog.ps1` read.
Exactly one of the two flags is required.

| Part of the file | Comes from |
|---|---|
| `projects.*` (compose file, `env_file` - `null` for every plane since `sl-env-split` - and the command line) | `stack.manifest.toml`. A `manual` plane (the portal) is deliberately absent: the watchdog must not auto-repair a plane a human starts by hand. `file: null` marks a project that owns no services - the anchor - and the watchdog skips those instead of issuing `up -d` into the void |
| `container`, `service`, `profile` | the compose **render**, with every declared profile switched on |
| `profile` | the render, EXCEPT where the plane's compose file is a pinned submodule that does not carry the profile yet - there the sidecar's value is a DECLARATION (see below) |
| `project` | the render too - a sidecar row may omit it. Record it only where the render cannot answer: a project whose compose file may be absent (`open-brain` - CI has no submodule). Where recorded, it is audited against the render |
| the plane GROUPING and row order; `critical`, `host_health`, `stale_pool_guard`, `note` | `scripts/lib/stack-services.curated.json`, hand-owned. Edit **that** file, then `--write` |

So the minimum a new service needs in the sidecar is its **plane group**, its
`container` name and its `critical` flag (plus `host_health` if it has one) -
which is exactly what `SERVICE-LIFECYCLE.md` step 8 tells you to write. A
tester followed that step literally and the generator refused, because the row
carried no `project` and nothing filled it in; `project` is derived now, and
the refusal that remains is the honest one - a container in **no** render, with
no project to attribute it to, is named and explained.

The render is also the verifier. `--check` fails, naming the row, on: a
container in a render that the sidecar does not list (`--write` refuses outright
- only a person can say which group it joins); a row whose project the render
contradicts; a stale `service` or `profile` recorded in the sidecar; a plane with
no project; a `manual` plane given one; a published host port the manifest's
`[planes.*.ports]` does not declare, or a declared port nothing publishes; and
the profile rules above.

#### `[declared, not rendered]` - a pinned submodule that has not caught up

A plane whose compose file lives in **this** repo must agree with the manifest:
both land in the same commit, so a manifest-declared profile the render does not
carry is drift, and a curated `profile` on a row the render gives no profile for
is stale. `ob1` is different - `OB1/docker/docker-compose.yml` comes from a
submodule pinned by gitlink, so the manifest can legitimately describe the branch
the gitlink will move to.

That is how `research`, `wiki` and `notebook` were carried between 2026-09-19
(sl-ob1-profiles put them in the manifest) and 2026-09-20 (sl-ob1-gitlink bumped
the gitlink `5005197` -> `fe3e045`): `inventory --check` printed those three, and
the container rows that named them, as `[ ~~ ] declared, not rendered` and
**passed**. **The bump has happened**, so the render carries all four and every
row is verified for real - `unpinned_profiles` returns the empty set for every
plane today. The submodule set is read from `.gitmodules`, so this is not an `ob1`
special case in the code - and a curated `profile` the MANIFEST never declared is
still drift, so the exemption cannot launder a typo.

**The one thing the operator owes at that bump**, which `--check` said out loud on
every run until it happened: declare the full profile set once, or `up` starts
fewer containers than are running. At the pinned gitlink the bare OB1 render is
<!-- stack:count:ob1:bare -->**20** services with no profile<!-- /stack:count:ob1:bare -->, all four profiles render
<!-- stack:count:ob1:all -->**30** services with every profile (`idea-refinery`, `research`, `wiki`, `notebook`)<!-- /stack:count:ob1:all -->, and a driver with no ob1
entry in its state passes `idea-refinery` + `research` only, which RENDERS
<!-- stack:count:ob1:default -->**23** services with `idea-refinery` + `research` (the driver's default)<!-- /stack:count:ob1:default -->. Either
`python scripts/stack/stack.py enable research`, which merges all four into
`.stack/state.json` and leaves every other enabled plane alone, or
`COMPOSE_PROFILES=research,wiki,notebook,idea-refinery` in `OB1/docker/.env`, which
compose honours natively and which `effective_profiles` unions into the driver's
own flags. Neither is set on this host yet. **Use `enable`, not `init --force`**
- on a host that already has a state file the two are not interchangeable, and
`init --force` would silently drop the planes that file already names. The
measured difference is under `init` below.

**Rendering with every profile is the point.** The check this replaced rendered
without any, so it could not see a profile-gated container at all - which is how
`openbrain-idea-refinery`, running on this host, stayed absent from the inventory
and un-repairable by the watchdog for as long as that check existed.

A project whose compose file is not on disk - CI does not check out the OB1
submodule - is carried from the sidecar and printed as `NOT VERIFIED` by name.
A check that cannot run must never look like one that passed.

### `docs --write` | `--check` [`--allow-unverified`]

The documentation's countable facts - services per plane and per profile set,
container names, profiles, published host ports, networks, the product menu,
the health probes - are not written by hand. Each sits between two HTML comments
in the doc, an opening `stack:<name>` and a closing `/stack:<name>`, and
`docs --write` fills it; `docs --check` changes nothing and exits 1 naming every
block that differs. Prose stays prose: edit around a block, never inside it.

| Block | Shape | What it holds |
|---|---|---|
| `plane-table` | lines | every plane: compose project, file, services under each profile set, published host ports, what starts it |
| `product-menu` | lines | every product: planes in `up` order, the profiles `enable` writes, surfaces, the manifest keys - resolved by `product_plan()`, the function `enable` itself uses |
| `plane-services:<plane>` | lines | that plane's services: container, profiles, host ports, networks |
| `profile-counts:<plane>` | lines | that plane's service count with no profile, the driver's default, each profile (with its `requires`) and every profile, and what each adds |
| `health-probes` | lines | the probe list, read out of `HealthSweep.run()` with every host answer stubbed, and which probes are conditional |
| `count:<plane>:<set>` | one line | one count WITH its condition; `<set>` is `bare`, `default`, `all` or profiles joined by `+` |
| `health-count` | one line | the probe count and the conditions it holds under |
| `profiled-planes` | one line | which planes declare profiles, from the manifest |

**Every count names the condition it was taken under.** A profiled plane has
several true service counts, one per profile set; a number printed without its
set is what made two docs look contradictory. Renders use each
plane's `.env.example` (`render_env_path`) in a scrubbed environment
(`render_capture`: this shell's `COMPOSE_PROFILES` and exported variables never
reach compose), and a generated block that would carry this checkout's path or
a secret-shaped value from a real `.env` is refused - the docs come out the same
on any machine.

**Which file carries which block is registered** in `DOCS_BLOCKS` in `stack.py`.
A registered block whose markers were deleted, half-deleted or mangled, a marker
that does not pair, an unregistered block, and a one-line block written across
lines are all failures, named by file and line - a block that silently stops
being generated is the failure this check exists for.

A `stack:` marker in a tracked `*.md` the registry does NOT name is refused
too: nothing would generate or compare it, so a hand-written number inside it
would read as generated.

A block whose input is not on this machine - the OB1 submodule not checked out,
**an OB1 checkout that is not at the STAGED gitlink or carries tracked edits**
(untracked files inside it are ignored), a gitignored env file compose stats, no
docker - is `NOT VERIFIED`, named, and left as committed; the text says it was NOT
compared. A `plane-table` is compared ROW BY ROW: only the rows of the planes that
cannot be rendered are kept as committed (`PARTLY VERIFIED`), so a hand edit of
any other row is still STALE. `--check` then exits **3**, never 0, unless
`--allow-unverified`.

**CI's `stack-driver` job is the gate that never skips**: it checks out the OB1
submodule, has docker, and runs `docs --check` WITHOUT `--allow-unverified`, so a
3 fails it. The pre-commit hook runs it (step 4b) whenever a registered doc, a
compose file, the manifest, an `.env.example`, `stack.py` or the OB1 gitlink is
staged; it refuses a 1 and records a 3 as a SKIPPED gate, saying what was not
compared and that CI compares it - a contributor without docker or OB1 is not
blocked, and not falsely told the blocks were checked. `docs --check` exits **4** instead of 3 when a gap
is an OB1 that IS checked out but not at the staged gitlink, or dirty. On a
**merge** commit the hook always runs and refuses a 1 or a 4 - both fixable by the
merger - but only warns on a 3 (no docker, no OB1 checkout, and likewise no
Python 3.11), so a newcomer's `git pull` is not blocked; CI compares those.
MERGE-PROTOCOL.md step 5 requires `--no-ff` and a `docs --check` exit 0. Git calls
aimed at the submodule drop git's repository-local variables
(`git rev-parse --local-env-vars`), which a hook sets for the PARENT repository.

### `labels` [`--dry-run`]

Sets the name Open WebUI shows for each local model **role** (`local-large`,
`local-large:nothink`, `local-small`, `local-small:nothink`, `local-embed` - the
`model_name`s in `inference/config/litellm/model_list/local.yaml`) to a label
**derived from the model file** the inference plane loads, so a model swap
relabels itself and no name is typed by hand (model-roles, operator decision
R4). The derivation is `scripts/stack/model_labels.py`:

- the role's `litellm_params.model` is the concrete id; a llama-swap id resolves
  through that entry's `--model` in `inference/config/llama-swap.config.yaml`
  (usually `${env.LLAMA_SWAP_..._MODEL_PATH}`), a `bge*` id through the embed
  upstream's `LLAMA_ARG_MODEL`;
- the variable is interpolated as compose does it - the shell, then
  `inference/.env`, then the `${VAR:-default}` in `inference/compose/upstreams.yml`
  - and the file is checked to exist under the service's `/models` bind;
- the label is the file stem with its quant split off, plus the mode:
  `Qwen3.8-27B-Q4_K_M.gguf` -> `Qwen3.8-27B Q4_K_M (thinking)` for `local-large`,
  `Qwen3.8-27B Q4_K_M (no thinking)` for the `:nothink` and `local-small` roles,
  `bge-m3 f16 (embeddings)` for `local-embed`.

`python scripts/stack/model_labels.py [--env-file F] [--skip-file-check]` prints
the labels and writes nothing.

The sync uses Open WebUI's own admin API (`GET /api/v1/models/model?id=`, then
`POST /api/v1/models/create` for a role with no row, or
`POST /api/v1/models/model/update` for one whose name differs) - the path its
admin UI takes to rename a base model. It reads and validates EVERY role row
before it writes any - so a refusal writes nothing - then writes only a name that
differs, sends a renamed row's meta, params, access grants and active
flag back exactly as it read them (Open WebUI 0.11.0 replaces a row's grants with
the list an update carries, and fails an update that carries none), and never
touches a row that is not a role id. A role id whose row is a PRESET (it has a `base_model_id`) is refused,
not rewritten. Each change is printed (`created as`, `renamed 'old' -> 'new'`,
`unchanged`).

It needs **`OWUI_ADMIN_API_KEY`** - an Open WebUI ADMIN user's API key (Settings >
Account > API keys; API keys must be enabled in Admin Settings > General) - from
the shell or the root `.env`. `OWUI_BASE_URL` defaults to `http://127.0.0.1:3000`.

**`up` and `recover` run it** after a successful run that started inference or the
frontend, when both are on (started by this run or enabled) and inference runs
`local` (from any source, as `health` decides it). There it never changes the
exit code: a failure prints `# labels: FAILED - ...` and a `WARNING` line naming
`labels`. Under `--dry-run` they print that they would run it and call nothing.
`stack.ps1` does not forward `labels`; run it with `stack.py`.

The `.env` is read the way compose reads it - a UTF-8 BOM (PowerShell 5.1's
`-Encoding utf8` writes one) is dropped, `KEY: value` is a key, an unquoted value
ends at ` #` - and a value whose meaning this reader will not guess (a `$` compose
would interpolate, a backslash escape in `"..."`, a quote that does not close on
its line) is a refusal naming the line, never a fallback to the compose default.

**Refuses** (exit 1): inference without `local` (no role is registered); a label
that cannot be derived (a variable with no value, a `.env` value as above, a path
outside `/models` or with a backslash in it, a missing file, a file whose symlink
leads outside the model store, a role forwarding an id no upstream serves, a
config file that cannot be read or decoded) - then nothing is written; no
`OWUI_ADMIN_API_KEY`; Open WebUI not answering `/health` within 180 s; a key Open
WebUI refuses (401/403); a preset on a role id or a row returned without its
grants - found while READING, so nothing is written.

**Can fail after writing** (exit 1, the error names the rows already written, each
of which carries its correct new label; the next run converges): a role row that
changed in Open WebUI between the read and its write (every row is re-read just
before its write); Open WebUI rejecting a write. One case repeats on every run: a
role row whose stored `meta` Open WebUI cannot parse answers **404** to the read,
so the sync plans a create and Open WebUI rejects it (HTTP 401 `Something went
wrong`, measured on a disposable 0.11.0) - repair or delete that row in Open WebUI (Admin Settings >
Models), then run `labels` again.

### `init` [`--planes a,b`] [`--product X`] [`--context plane=name`] [`--headless`] [`--force`]

Writes `.stack/state.json`. Non-interactive by design - it has to behave
identically on a fresh host, in CI and under a script.

- No flags: writes the default (`frontend`).
- `--product X` runs the **same checks** `enable X` does; a state file naming a
  plane whose key is blank is a bring-up failure deferred, not avoided.
- `--context plane=name` is repeatable; a malformed pair is refused.
- **Refuses** to overwrite an existing state file without `--force`.

**`init --force` REPLACES the state file; `enable` MERGES into it. To add a
product on a host that already has a state file, the verb is `enable`.** The two
read almost identically on screen - `init --product X` calls `cmd_enable`
internally, so it prints the same `enabled product X:` block - but `cmd_init`
builds a *fresh* `State({})` and saves that, while `cmd_enable` mutates the state
it loaded. Measured against two scratch state files, both seeded by enabling
`frontend, inference, memory, search, coder, agent-org` (six planes):

| | Before | Command | After |
|---|---|---|---|
| replace | those six | `init --product research --force` | `frontend, inference, ob1, search` - **four**. `memory`, `coder` and `agent-org` are gone from the file, and a later `up` no longer starts them. |
| merge | those six | `enable research` | `frontend, inference, memory, search, coder, agent-org, ob1` - **seven**, with `ob1` carrying `idea-refinery, research, wiki, notebook`. |

Both printed the same four-line `enabled product research:` summary naming
`inference, frontend, search, ob1`; only the resulting file differs, and only
`init` adds the `wrote <path>` line and the `list` dump after it. Without
`--force`, `init` refuses an existing file outright:
`refused: <path> already exists (re-run with --force to overwrite it)`, exit 1.

So `init --force` is for a host whose state you intend to start over from - a
fresh machine, a scratch tree under `--root`/`--state`, or a deliberate reset.
`enable` is for everything else.

---

## Global flags

| Flag | Default |
|---|---|
| `--root <dir>` | two directories above this script (the repo root) |
| `--manifest <file>` | `<root>/stack.manifest.toml` |
| `--state <file>` | `<root>/.stack/state.json` |

`--root` is what lets you dry-run against a scratch tree (a deliberately blank
key, say) without touching the real `.env`. Never edit the real `.env` to test a
refusal.

---

## Tests

```text
python -m pytest scripts/stack -q
```

Hermetic: every test builds a throwaway root from the **real**
`stack.manifest.toml` with placeholder compose files and generated env files,
and injects a recorder in place of the docker call. A test that would have
started a container leaves a command in a list instead. The suite pins the
`stack.ps1` ordering, the requires/optional edge sets, every refusal, the
product expansions, `--headless`, the context prefix, and the stdlib-only rule.

`health` runs against a scripted host (`FakeHost`) - every `docker` call and
every HTTP GET is answered from a fixture, so the probe set, the exit code and
the "one dead plane does not end the sweep" rule are all testable with no
daemon. The **inventory generator** is tested against a small manifest of its
own (`MINI_MANIFEST`) with a scripted `docker compose config`, so an unrelated
plane change cannot fail it; the shipping tree is covered by three `shipped`
tests plus `inventory --check` itself, which the pre-commit hook and the
`stack-driver` CI job both run for real.

`labels` and the model-role label generator (`test_model_labels.py`) run
against a scratch copy of the real inference config and a `FakeOwui` that
answers the four Open WebUI admin-API calls as 0.11.0 does; their real proof is
the disposable Open WebUI in item `mr-gateway`'s test plan.

`recover`, `backup`, `restore` and `stats` run against `OpsDaemon`, a scripted
daemon with containers, volumes and the helper container, so gate timeouts,
restart loops, a tampered archive and a busy volume are all testable with no
daemon. Their real-daemon proof is `scripts/stack/rehearse-ops.sh`: in an
isolated Docker-in-Docker it brings up the stock frontend, backs up its data
volume, destroys it, is refused on a tampered archive and on a running
container, restores it, and checks the marker and `/health`; then `stats`,
`recover frontend`, and a recover that meets a planted unhealthy container.
(`rehearse-fresh-clone.sh` is its sibling for a fresh clone's first `up`.)

`.github/workflows/ci.yml` runs `python -m pytest scripts/stack -q`,
`python scripts/stack/stack.py inventory --check` and
`python scripts/stack/stack.py docs --check` on Python 3.12, with the OB1 submodule
checked out.
The docs generator's block renderers and marker scanner are tested against
`MINI_MANIFEST` with a scripted render (`DocsHost`).

## Not in this item

Porting `stack-watchdog.ps1`, or `emergency-recovery.ps1`'s `nuclear` and
`gpu-reset` modes (its `recover` mode is `stack.py recover` since
`ac-ops-portable`); both scripts still carry their own ordering. Mirroring
backups to a NAS (`backup-to-nas.ps1` stays the Windows path). Executing against remote docker
contexts - the `--context` prefix is passed through and nothing more
(`cluster-transition`). Archiving `stack.ps1`: it stays as the shim until every
caller has moved. Adding compose profiles to a plane - both of the
gaps this paragraph used to name are now closed: `frontend`'s `stock`/`gpu`/
`tailscale` are live in `frontend/docker-compose.yml` (`sl-frontend-solo`) and
`ob1`'s `research`/`wiki`/`notebook` are live in the pinned submodule since the
gitlink bumped to `fe3e045` (`sl-ob1-gitlink`, 2026-09-20), so *[declared, not
rendered]* above describes a mechanism no plane currently needs. Per-plane
`.env` files (`sl-env-split`): this item still encodes the single root `.env`.
