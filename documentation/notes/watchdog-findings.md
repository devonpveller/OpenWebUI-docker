# Watchdog findings — out-of-scope observations from `watchdog-fix` (2026-08-28)

Sink for the `watchdog-fix` work item (anchor: `queue.ps1 -Show -Id watchdog-fix`).
Everything here is true but deliberately **not** fixed by that item.

**Provenance is stated per entry** and means exactly this:
- *read from source at `file:line`* — the code path was opened and read, not the
  comment above it.
- *observed live on `<date>`* — a command was run against the running stack on
  that date and has not been re-run since.
- *not verifiable from this tree* — a third-party binary or image internal; check
  before acting.

---

## 1. `emergency-recovery.ps1` has the SAME defect class, and my first report of it was WRONG

**Provenance: read from source at `scripts/recovery/emergency-recovery.ps1`, lines
as listed, 2026-08-28.**

The `watchdog-fix` anchor originally claimed this script was "CHECKED, not
assumed" and clean. **That claim was false and has been corrected in the anchor.**
It contains 53 code-level `docker compose` invocations, of which **16 carry no
`-f`** and therefore address the root anchor project, which declares zero
services.

Of those 16, these **10 name a service or container**, which the anchor project
cannot resolve:

| Line | Invocation |
|---|---|
| 171 | `docker compose exec $Container ping -c 1 -W 5 8.8.8.8` (`Test-NetworkConnectivity`) |
| 184 | `docker compose stop @Services` (`Stop-ServiceGroup`) |
| 197 | `docker compose up -d @Services` (`Start-ServiceGroup`) |
| 567 | `docker compose stop $ServiceName` |
| 572 | `docker compose ps $ServiceName --format json` |
| 582 | `docker compose kill $ServiceName` |
| 599 | `docker compose ps $ServiceName --format json` |
| 778 | `docker compose exec gateway curl -s http://localhost:8080/healthz` |
| 836 | `docker compose ps caddy --format json` |
| 974 | `docker compose up -d llama-cpp-embed-upstream` |

The remaining six bare invocations take **no service argument** and are therefore
correct as written — they address the anchor project itself: L153
`docker compose version`, L373 `docker compose ps --format json`, L638
`docker compose ps`, L689 `docker compose up -d`, L866 `docker compose down`,
L874 `docker compose up -d`.

**What is NOT asserted here.** What these calls do to that script's control flow
has **not** been verified — the script was not executed, and no claim is made
about which recovery paths degrade or how. The PS 5.1 silent-failure mechanism
proven for the watchdog (see §2) plausibly applies, but "plausibly" is not
"verified", and the next person should establish it rather than inherit it.

**How the false claim happened, because the mechanism matters more than the
error.** The verifying grep was piped through `head -20`, and twenty comment
lines filled the entire budget. No code line was ever displayed, and "no matches
shown" was read as "no matches exist". The tool answered a narrower question than
the one it was asked, and the answer was taken for the question. Any
"checked and clean" claim about a file needs the *complete* result, with comment
lines excluded rather than allowed to crowd out the code.

## 2. The PS 5.1 native-stderr trap is why nobody noticed for a week

**Provenance: observed live 2026-08-28** (executed, not reasoned about):

```
$ErrorActionPreference='Stop'
try { docker compose up -d --dry-run open-terminal | Out-Null } catch { 'THREW' }
#  -> threw=False   lastexit=1
```

An **un-redirected** native stderr write does **not** raise a terminating error
under `$ErrorActionPreference='Stop'`. The surrounding `catch` never fires. So a
repair that never ran produced no ERROR line, and the log showed only the generic
"recovery failed" that a genuinely-attempted-but-unsuccessful repair produces.

The inverse is also true and is the more familiar half: a **redirected** native
stderr (`2>$null`, `2>&1`) under `Stop` *does* become a terminating
`NativeCommandError`. Both halves are live in this repo. `check-watchdog-repair-targets.ps1`
sets `$ErrorActionPreference = 'Continue'` for exactly this reason.

## 3. `Repair-TailscaleService` crashed on a real recovery attempt

**Provenance: observed live in `logs/tailscale-health.log`, 2026-08-25 16:28:53;
not re-run since, and the failing call was not located in source.**

```
2026-08-25 16:27:02 [WARN]  Tailscale container not running, starting...
2026-08-25 16:28:01 [WARN]  Network connectivity failed, attempting recovery...
2026-08-25 16:28:01 [WARN]  Starting Tailscale service recovery...
2026-08-25 16:28:53 [ERROR] Recovery failed with error: A parameter cannot be found that matches parameter name 'and'.
```

That message is PowerShell parameter binding, so a call is passing a bare word
`and` where a parameter is expected — an unquoted string reaching a cmdlet, most
likely. This fired on a genuine outage, so tailscale recovery failed when it was
actually needed. **The offending line was not identified** — it should be found
before this is called fixed. Separate item.

## 4. `stack.ps1` cannot see `open-terminal` at all

**Provenance: read from source at `scripts/stack/stack.ps1:42-49`, plus the
pre-existing note in `coder/README.md`.**

`stack.ps1` contains **zero** `docker compose` invocations — it is table-driven
from a `$Planes` list of `@{ Name; Compose; Note }` entries. It therefore does
not have the bare-invocation defect, but for a different reason than the
`watchdog-fix` anchor first gave: the anchor said it "drives planes through
per-plane `$Script:*Compose` path variables", which is `emergency-recovery.ps1`'s
shape, not this one. Corrected here.

Separately, `coder/README.md` records that `stack.ps1 health` has no probe for
`open-terminal:8000`. Combined with the watchdog defect this item fixed, the
operator had **no** automated surface that could both see and repair the
executor. The health-probe gap is still open.

## 5. The plane→compose-file mapping now exists twice

**Provenance: read from source at `scripts/checks/check-project-configs.ps1:67-77`
and `scripts/lib/stack-services.json` (`projects`, as extended by this item).**

`check-project-configs.ps1` hardcodes its own `$renderTargets` table of the five
plane compose files (with `--env-file .env.example`, CI-safe, and OB1 with no
`--env-file`). That table is now duplicated by the `projects` map this item added
to `stack-services.json`. The pre-commit check could read the registry instead.

**Not done here deliberately:** that script gates every commit in the repo, and
breaking it breaks committing. It should be changed on its own, with its own
test.

## 6. `new-worktree.ps1` does not copy the OB1 recipe env files

**Provenance: observed live 2026-08-28 in worktree `wt-watchdog-fix`, plus read
from source at `OB1/docker/docker-compose.yml:15-16` and
`OB1/docker/docker-compose.scheduled.yml:95,122`.**

Inside a worktree:

```
docker compose -f OB1/docker/docker-compose.yml config --quiet    # exit 1
#   env file .../OB1/recipes/email-history-import/.env not found
docker compose -f OB1/docker/docker-compose.yml config --services # exit 0, 28 services
```

`docker-compose.yml` `include:`s `docker-compose.scheduled.yml`, which declares
`env_file: ../recipes/email-history-import/.env`. That file is gitignored; it
exists in the main checkout but `new-worktree.ps1` copies only `.env`,
`.env.test` and `OB1/docker/.env`.

Two consequences worth knowing:
- **A full OB1 `config` render cannot be validated from a worktree.** Run it from
  the main checkout.
- **`config --services` and `config --quiet` are not equivalent.** `--services`
  lists service keys without resolving `env_file` contents, so it succeeds where
  a full render fails. Anything relying on `--services` as a proof of
  renderability is claiming more than it checked.

## 7. Scope note: this item touched a fourth file the anchor did not name

**Provenance: read from source at `coder/README.md`, gotcha 1, 2026-08-28.**

The anchor named three files. A fourth, `coder/README.md`, was edited because it
carried a "Gotchas (each verified against this tree)" entry asserting this defect
was **live and unfixed** — a prior documentation-only item had recorded it
accurately and said so explicitly. Landing the fix without touching it would have
shipped a tree that contradicts itself.

The edit is confined to that gotcha: it now says which parts are fixed, keeps the
line numbers and the manual-restart command, and adds that
`emergency-recovery.ps1` still carries the defect class. Flagged for the reviewer
as a deliberate departure from the anchor's file list rather than hidden in the
diff.
