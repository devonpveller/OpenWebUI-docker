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
`sl-ob1-profiles` had given that script's plane registry four OB1 profiles to keep
the thirty running containers starting; the registry is gone, and the same set is
expressed here instead - `idea-refinery` is `default` and `requires` `research`,
so `up` passes both, which against the pinned gitlink renders exactly the same
thirty services.
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
python scripts/stack/stack.py health              # the 15-probe sweep (read-only)
python scripts/stack/stack.py stats               # inference demand + queue board
python scripts/stack/stack.py inventory --check   # is stack-services.json still true?
```

`status`, `health`, `doctor` and `inventory --check` are **read-only**: they
start, stop and recreate nothing, so they need no plane lease.

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
| `env_file` | the `--env-file` argument. **Absent means absent**: compose then loads the `.env` sitting in the compose file's own directory. That is why `ob1` and `agent-org` pass none - their project directories are `OB1/docker` and `agent-org/docker`, and passing the root `.env` would override the right values with the wrong ones. |
| `lease` | the `scripts/agent-harness/lease-names.conf` name for the plane. Absent = no canonical lease name (the anchor). |
| `requires` / `optional` | see above. |
| `implicit` | the plane is started whenever anything runs and never has to be enabled. Only the anchor. No refusal ever names it, and `enable` never writes it into the state file. |
| `manual` | present when the driver must **not** start or stop this plane; the value names what does. Only the portal: exposing the stack to the internet stays a human action, exactly as `stack.ps1`'s header says. |
| `host` | what the machine itself must provide, in prose (a GPU, a tunnel, model files). `doctor` prints these; nothing enforces them. |
| `keys` | variable names that must exist and be non-blank in the plane's env file. A blank one makes `enable` refuse and name the key. |
| `ports` | published **host** ports -> what answers on them. |
| `profiles` | compose profiles, each a sub-table with a `description` and **exactly one** of the three flags below. |

#### Every profile says whether a default `up` starts it

| Flag | Means | Today |
|---|---|---|
| `default = true` | the driver passes `--profile <name>` on every invocation | `ob1`'s `idea-refinery` - parity with what `stack.ps1` always passed |
| `opt_in = true` | something **other than the driver** turns it on, and the description says what | `inference`'s `local` (`COMPOSE_PROFILES` in the root `.env`), `agent-org`'s `workers`/`cloud` (the operator - `stack.ps1`'s header always said these were not managed), `portal`'s `internet` (`portal-on.ps1`) |
| `pending = true` | declared here, **not yet in the compose file**; a later item adds it. Enabling one is a no-op and the driver says so | `frontend`'s `gpu`/`tailscale` |

Declaring none of the three is **refused** by `inventory --check`. That gate
exists because the dangerous change is a compose file gaining a profile around
services that are *already running*: the driver would then stop passing what
starts them, `up` would quietly bring up a smaller stack, and nothing would say
so. Forcing the declaration turns that into a decision someone made on purpose.

#### `--profile` REPLACES `COMPOSE_PROFILES`; it does not add to it

Measured 2026-09-19, compose v5.3.0, root `.env` carrying
`COMPOSE_PROFILES=local,gpu,tailscale`:

```text
docker compose -f inference/docker-compose.yml --env-file .env config --services
  -> 8 services            (the `local` half is on)
... --env-file .env --profile idea-refinery config --services
  -> 4 services            (`local` silently dropped)
```

So the moment the driver passes **any** flag to a plane, it must also pass every
profile that plane's env file already enabled, or it starts a subset of what a
bare invocation would have started - with no error anywhere. `effective_profiles()`
does exactly that union, and passing no flag at all stays safe because compose
then reads `COMPOSE_PROFILES` itself. That is today's path for every plane except
`ob1`.

### Product keys

| Key | Meaning |
|---|---|
| `description` | one line, shown by `list`. |
| `planes` | the planes the product needs. Their `requires` closure comes too. |
| `profiles` | `{ plane = ["profile", ...] }` - profiles to enable inside a plane. |
| `surfaces` | `{ plane = ["profile", ...] }` - how a person reaches the engine. Dropped by `--headless`; a plane that appears **only** under `surfaces` is itself dropped by `--headless`. |

Five names (`inference`, `memory`, `search`, `agent-org`, `portal`) are both a
plane and a product. A bare name resolves to the **plane**, because that is the
smaller action and the one whose refusal matters: `enable memory` must refuse
while inference is off rather than quietly enabling inference too. Force the
other reading with `--product <name>` (or `--plane <name>`). **Both `enable` and
`disable`** print a `# note:` line whenever a name is ambiguous, saying which
reading they took - `disable` is the destructive half of the pair, so it is the
one where a silent reading would be worse.

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
    "frontend": { "profiles": [], "context": null },
    "inference": { "profiles": [], "context": "optiplex-1" }
  }
}
```

`context` is the Docker context the plane runs on; when set, the command becomes
`docker --context optiplex-1 compose -f ...`. That is the data the
cluster-transition phase reads; this item does no remote logic beyond the prefix.

An unreadable state file is refused, not ignored - a silently-defaulted state
would start the wrong set.

---

## Verbs, with their refusal cases

### `list`

Planes with `enabled` / `disabled`, the profiles and context of the enabled
ones, the `up would start:` line, and the products. Read-only, no docker.

### `status` / `up` / `down` - which planes?

All three take the same selection:

| Form | Acts on |
|---|---|
| *(nothing)* | the planes this machine **enables**. `up`/`down` add their `requires` closure; `status` does not - reporting on a plane nobody enabled is noise |
| `<plane>` | exactly that plane. `up <plane>` prints a `#` note naming any requirement it is **not** starting |
| `--all` | every plane the manifest declares except the `manual` ones - what a bare `stack.ps1 up` meant |

A plane name together with `--all` is refused.

### `status`

`docker compose ... ps` per selected plane, in dependency order. Read-only:
no lease needed, nothing is started, stopped or recreated. Exits 1 if any `ps`
exits non-zero.

### `up` / `down` [`--dry-run`]

`up` starts the selected planes in dependency order; `down` stops them in
reverse. `--dry-run` prints the exact
`docker [--context X] compose -f <file> [--env-file ...] [--profile p]... <verb>`
lines and runs **nothing**.

A `manual` plane (the portal) is never started or stopped; a `#` comment line
after the commands names the script that drives it.

If a docker command exits non-zero the run stops there and reports which plane
and which code - the rest is not attempted.

**Refuses:** nothing. An empty enabled set just prints a `#` note.

### `restart <plane>` [`--dry-run`]

**Refuses** `restart all` (naming `down` + `up` and
`scripts/recovery/emergency-recovery.ps1`, which layers health gates on the same
order). **Refuses** a `manual` plane, naming its script. Restarting a plane that
is not enabled prints a note and proceeds.

### `enable <plane|product>` [`--headless`] [`--plane`|`--product`]

A **plane**: enables just that plane (plus its `default` profiles).

- **Refuses** when a required plane is not enabled, naming it *and* the command
  that would enable it:
  `refused: memory requires inference, which is not enabled (python scripts/stack/stack.py enable inference)`
- **Refuses** when one of the plane's `keys` is blank or missing in the env file
  that plane reads, naming the key, whether it is blank or missing, the file, and
  the remedy:

  ```text
  refused: search needs these keys before it can be enabled:
    MULLVAD_WG_PRIVATE_KEY is blank in .env
  Set them in .env, then re-run (`python scripts/stack/stack.py doctor` lists every blank key on this machine).
  ```


A **product**: enables its planes, their `requires` closure, its `profiles`,
and its `surfaces` unless `--headless`.

- A product does **not** refuse on its own members being off - expanding them is
  the point. It still **refuses** on any member plane's blank key, naming the
  key *and* which plane reads it.
- `--headless` drops the surface profiles, and drops any plane that is only a
  surface (`coding-agent`'s frontend). A plane listed under both `planes` and
  `surfaces` stays (`research`'s frontend).

Enabling a `pending` profile is allowed and prints a note saying it changes
nothing until the item that adds it to the compose file lands.

### `disable <plane|product>`

**Refuses** while an enabled plane still requires the one being disabled,
naming the dependents. Disabling something that is not enabled is a no-op note.

### `doctor`

Reports docker on PATH, `docker compose version`, the Python version, the
manifest and state paths, and then per enabled plane: the compose file exists,
the env file exists (and how it is loaded), every blank or missing key, and the
plane's `host` requirements. Exits 1 if anything is `[FAIL]`. Read-only.

### `health`

The fifteen functional probes `stack.ps1 health` ran, one for one, with the same
pass conditions, the same `[OK]` / `[FAIL]` line shape and the same exit code:
**the number of failed probes**. Read-only - `docker ps`, `docker network
inspect`, **five** read-only `docker exec`s (`llm-gateway`, `tailscale`,
`little-coder`, `openbrain-db`, `agent-bridge`), one `powershell -File
check-owui-drift.ps1 -CountOnly`, and **seven** HTTP GETs - `:3000/health`,
`:8060/health`, `:8085/healthz`, `:8085/health`, `:5055/api/config`,
`:8062/health`, `:8816/health`. Seven, not six, because `search` gets two of
them; that is the whole point of the third rule below.

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

### `stats`

Hands off to `scripts/stack/stack-stats.ps1` (the llm-queue `/observe` board and
the LiteLLM spend ledger, read-only). That script is PowerShell 5.1 only, so off
Windows this verb **refuses** and names what to run instead - a verb that prints
nothing and exits 0 is the failure class this repo hunts.

### `inventory --write` | `--check`

Generates `scripts/lib/stack-services.json`, the inventory
`scripts/recovery/status_check.py` and `scripts/checks/stack-watchdog.ps1` read.
Exactly one of the two flags is required.

| Part of the file | Comes from |
|---|---|
| `projects.*` (compose file, `--env-file`, the command line) | `stack.manifest.toml`. A `manual` plane (the portal) is deliberately absent: the watchdog must not auto-repair a plane a human starts by hand. `file: null` marks a project that owns no services - the anchor - and the watchdog skips those instead of issuing `up -d` into the void |
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

Today `stack.manifest.toml` declares `research`, `wiki` and `notebook` on that
plane (sl-ob1-profiles) while the pinned commit `5005197` declares only
`idea-refinery`. `inventory --check` prints those, and the nine container rows
that name them, as `[ ~~ ] declared, not rendered` and **passes**; the moment the
gitlink bumps, the render carries them and every one is verified for real. The
submodule set is read from `.gitmodules`, so this is not an `ob1` special case in
the code - and a curated `profile` the MANIFEST never declared is still drift,
so the exemption cannot launder a typo.

**One thing the operator owes at that bump**, which `--check` says out loud every
time until it happens: run `python scripts/stack/stack.py init --product research
--force` (or `enable research`) once. Until the bump, `wiki` and `notebook` gate
nothing - compose ignores a profile it does not know, and `up --all` starts the
same thirty OB1 containers it does today (measured). After it, they gate seven
running containers that a bare `up` would no longer start.

**Rendering with every profile is the point.** The check this replaced rendered
without any, so it could not see a profile-gated container at all - which is how
`openbrain-idea-refinery`, running on this host, stayed absent from the inventory
and un-repairable by the watchdog for as long as that check existed.

A project whose compose file is not on disk - CI does not check out the OB1
submodule - is carried from the sidecar and printed as `NOT VERIFIED` by name.
A check that cannot run must never look like one that passed.

### `init` [`--planes a,b`] [`--product X`] [`--context plane=name`] [`--headless`] [`--force`]

Writes `.stack/state.json`. Non-interactive by design - it has to behave
identically on a fresh host, in CI and under a script.

- No flags: writes the default (`frontend`).
- `--product X` runs the **same checks** `enable X` does; a state file naming a
  plane whose key is blank is a bring-up failure deferred, not avoided.
- `--context plane=name` is repeatable; a malformed pair is refused.
- **Refuses** to overwrite an existing state file without `--force`.

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

`.github/workflows/ci.yml` runs `python -m pytest scripts/stack -q` and
`python scripts/stack/stack.py inventory --check` on Python 3.12.

## Not in this item

Porting `emergency-recovery.ps1` or `stack-watchdog.ps1` to Python; both still
carry their own ordering and their own probes. Executing against remote docker
contexts - the `--context` prefix is passed through and nothing more
(`cluster-transition`). Archiving `stack.ps1`: it stays as the shim until every
caller has moved. Adding compose profiles to a plane - the manifest still only
*declares* `frontend`'s pending `gpu`/`tailscale` (`sl-frontend-solo`) and
`ob1`'s `research`/`wiki`/`notebook` are real in the manifest since
`sl-ob1-profiles`, and not yet in the pinned submodule - see
*[declared, not rendered]* above. Per-plane
`.env` files (`sl-env-split`): this item still encodes the single root `.env`.
