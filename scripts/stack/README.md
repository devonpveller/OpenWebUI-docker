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

`stack.ps1` is untouched by this item and remains the driver in use; `stack.py`
reproduces its ordering exactly (see *Ordering* below) so the two agree while
`sl-driver-parity` moves health, stats and the inventory generator across.

---

## Quick start

```text
python scripts/stack/stack.py list             # what exists, what is on
python scripts/stack/stack.py init             # write a state file (default: frontend)
python scripts/stack/stack.py enable research  # turn a product on
python scripts/stack/stack.py up --dry-run     # the exact docker lines, run nothing
python scripts/stack/stack.py up               # start them, in dependency order
python scripts/stack/stack.py doctor           # docker, env files, blank keys
```

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
| `profiles` | compose profiles, each a sub-table: `description`, optional `default = true` (passed on every invocation - `ob1`'s `idea-refinery`, for parity with `stack.ps1`), optional `pending = true` (declared here, **not yet in the compose file**; a later `stack-layers` item adds it, and the driver says so when you enable one). |

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

### `status`

`docker compose ... ps` for each enabled plane, in dependency order. Read-only:
no lease needed, nothing is started, stopped or recreated. Exits 1 if any `ps`
exits non-zero.

### `up` / `down` [`--dry-run`]

`up` starts every enabled plane and everything they require, in dependency
order; `down` stops them in reverse. `--dry-run` prints the exact
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

## Not in this item

Health probes, `stats`, and the generated services inventory
(`sl-driver-parity`). Adding compose profiles to any plane - the manifest only
*declares* the pending ones (`sl-frontend-solo`, `sl-inference-split`,
`sl-ob1-profiles`). Per-plane `.env` files (`sl-env-split`): this item encodes
today's single root `.env`. The cross-node forwarder and any remote deploy logic
(`cluster-transition`).
