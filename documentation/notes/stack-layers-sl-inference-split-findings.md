# Findings — stack-layers `sl-inference-split` (2026-09-19)

Findings sink for the harness item that split `inference/docker-compose.yml` into
`include:`d service groups, put the local backends behind a `local` profile, made
cloud providers key-gated and replaced the two literal model-store binds with
`${LM_MODELS_DIR}`.

Everything below was checked in the worktree `wt-sl-inference-split` on
2026-09-19 against base `development` @ `b28cbc5`. Commands and outputs are
quoted where the claim depends on them.

---

## F1 — `llm-gateway` has no egress, so a cloud model it lists is not reachable

`inference/compose/gateway.yml` puts `llm-gateway` on `llm-net` and
`llm-backend-net` only. In `inference/docker-compose.yml`, `llm-net` is
`external: true` -> `ai-stack_llm-net`, which the anchor declares internal
(stack-map reference section 1 network table: "internal (no internet)"), and
`llm-backend-net` is declared `internal: true` in the spine file itself. The
gateway's own compose comment says this is deliberate: *"Keeping the gateway off
any internet-capable bridge is the supply-chain posture (guide section 19 — no
outbound)."*

So the mechanism this item was asked for — cloud models appear when their key is
set — makes them **LISTED, not REACHABLE** on the operator's current topology.
Proof that the listing half works is in the test plan (T3/T4); the reachability
half would need `llm-gateway` on a non-internal network, which is a deliberate
reversal of the section 19 posture and was **not** done here.

Shipped state: `OPENROUTER_API_KEY=` (blank) in `.env.example`, and the variable
is absent from both the worktree `.env` and the operator's `.env` (checked with
grep on 2026-09-19). So nothing is listed today and nothing changed
operationally.

## F2 — this contradicts an existing decision (agent-org OD-6), which is an operator call

`agent-org/config/litellm-cloud.config.yaml` lines 8-11 state the opposite policy
in as many words:

> The LOCAL llm-gateway is a DIFFERENT, air-gapped instance
> (config/litellm.config.yaml). Do NOT add OpenRouter to it; do NOT add these
> models to it. Two lanes, two gateways (PLAN 3.4 / OD-6).

agent-org runs its own LiteLLM (`llm-gateway-cloud`, profile `cloud`) behind
`ao-egress` with its own master key and per-role budgets.

stack-layers D11 asks for the opposite capability — that the inference plane's
gateway be able to serve cloud models where no local backend exists. Both can be
true at once (D11 is about a node with no GPU; OD-6 is about agent-org's
governance lane), but nobody has decided whether the inference plane's gateway is
ever the thing that carries a cloud lane.

**This item built the mechanism and left it inert** (blank key = zero cloud
models registered), and wrote the warning into
`config/litellm/model_list/cloud.openrouter.yaml` and next to
`OPENROUTER_API_KEY` in `.env.example`. The decision is the operator's.

## F3 — OPERATOR ACTION REQUIRED BEFORE THE NEXT `up` OF THE INFERENCE PLANE

After this merges, `.env` needs

```
COMPOSE_PROFILES=local
```

or `docker compose -f inference/docker-compose.yml --env-file .env up -d` (and
`scripts/stack/stack.ps1 up inference`, and `emergency-recovery.ps1`) brings up
**four** services — the gateway, its Postgres, the Admin UI and the ledger backup
— and no inference backend at all. The plane would look healthy and serve
nothing.

Verified on 2026-09-19 that an env file is enough (scratch project, Compose
v5.3.0): `docker compose --env-file <file with COMPOSE_PROFILES=local> config
--services` lists the profiled service, and the same value interpolates into a
service's `environment:`. One line in `.env` does both jobs.

**The recovery and driver scripts were deliberately NOT changed.** Adding
`--profile local` to `stack.ps1` / `emergency-recovery.ps1` would start the
backends while leaving `COMPOSE_PROFILES` unset *inside* `llm-gateway`, so the
gateway would register no local models: backends up, nothing listed. The
variable, not the flag, is the mechanism. A script that exported
`$env:COMPOSE_PROFILES` would work, but it hardcodes this host's answer into the
shared driver, which belongs with `sl-manifest` / `sl-driver-parity`.

## F4 — a `:?` guard cannot protect a variable only a profiled service uses

Measured 2026-09-19 on Compose v5.3.0 with a two-service scratch project (service
`a` profiled `local` and binding `${LM_MODELS_DIR:?set LM_MODELS_DIR}`, service
`b` unprofiled):

```
docker compose config --services        # var unset, profile NOT active
error while interpolating services.a.volumes.[]: required variable LM_MODELS_DIR
is missing a value: set LM_MODELS_DIR
```

Interpolation runs over the whole file **before** profile filtering, so a `:?`
guard on a `profiles: [local]` service breaks the cloud-only render as well.
Hence `${LM_MODELS_DIR:-../../data/models/gguf}` rather than the `:?` form the
anchor's artifact line sketched — the anchor anticipated this and allowed the
`:-` default; this is the measurement behind taking it.

Consequence to know: an operator who deletes `LM_MODELS_DIR` from `.env` and runs
with the profile on gets an empty `/models` and a llama-swap that cannot find its
GGUF — loud at the model level, not at the compose level.

## F5 — the assembler governs the YAML path only; `store_model_in_db: true` is a second door

`config/litellm.config.yaml` keeps `general_settings.store_model_in_db: true`
(untouched by this item), and `llm-gateway-ui` exists so models can be managed
from the Admin UI. A model added through the UI/API is stored in `llm-gateway-db`
and served **without** passing through `config/litellm/assemble-config.py`.

So "never register a model whose backend is absent" is enforced for the config
files and not for the DB path. Nothing here regresses — this was already true —
but the rule should not be read as absolute, and a cloud-only node whose operator
adds a local model in the Admin UI would get exactly the failure D11 is trying to
prevent.

## F6 — `check-project-configs.ps1` now has zero drift coverage on the four profiled rows

`scripts/checks/check-project-configs.ps1` renders inference as
`@{ P = 'inference'; F = 'inference\docker-compose.yml'; A = @('--env-file', '.env.example') }`
— no profile. Its `stack-services.json` drift verifier extracts `container_name`
from that render, so the four profiled containers no longer appear in it. The
check still passes (it flags only MISSING and WRONG rows, never EXTRA), and
`scripts/lib/stack-services.json` keeps its four rows, now carrying
`"profile": "local"` — the same key the `agent-org` rows already use for
`workers` / `cloud`, so the shape is not new.

But the guard that would catch a rename of `llama-cpp-upstream` in compose
without the matching inventory edit is now blind to those four. The fix is a
second render target with `--profile local`; it is not in this item's artifact
and touches a checks file, so it is recorded here. Note `agent-org`'s profiled
services have the identical hole today — this is a pre-existing class the split
extends to one more plane, not a regression unique to it.

## F7 — `ruff check .` fails on `development`, before this item's changes

```
E501 Line too long (103 > 100)
  --> llm-queue\src\llm_queue\__init__.py:9:101
```

`git status --short -- llm-queue/` is empty in this worktree, and
`git show development:llm-queue/src/llm_queue/__init__.py | sed -n '9p' | wc -c`
returns `104` (103 characters plus the newline). Pre-existing on the base commit;
the line is a doc pointer rewritten by the 2026-09-18 plan-store move, which
lengthened it past the subproject's 100-column limit.

`ruff check config/litellm/assemble-config.py` (this item's only new Python)
returns `All checks passed!`.

Not fixed here: it is another plane's file, and a wrap in it would be an
unrelated change inside this diff.

## F8 — two runbooks still hardcode the model-store path

`${LM_MODELS_DIR}` is now the single source of truth for the compose binds, but
the literal path survives in prose:

- `documentation/runbooks/backup-restore-runbook.md:162`
- `documentation/runbooks/restore-from-snapshot.md:210,275,277,278` — including a
  `Remove-Item -Recurse -Force` line over that path that a reader would paste.

They are correct today (that is exactly the value `.env.example` documents), but
an operator who repoints `LM_MODELS_DIR` would wipe the wrong directory, or the
right one for the wrong reason. Out of this item's artifact; `sl-readmes`
(wave 4) is the doc item.

## F9 — the stale header of `config/litellm.config.yaml` was left alone, on purpose

Lines 1-18 still say the gateway is PERMISSIVE with "master_key intentionally
absent" (false since J.1, 2026-08-21 — the file's own `general_settings` sets
`master_key: os.environ/LITELLM_MASTER_KEY` a few lines below) and that "api_base
below points at the renamed real servers" (false since B2, 2026-06-14 — it points
at `llm-queue`). `sl-closeout` is fixing those lines in parallel, so this item did
not touch them.

Worth knowing for whoever merges second: **this item's diff to that file starts at
line 19**, two lines after `sl-closeout`'s line-17 target, and the hunk's context
begins at line 16. A three-way merge may or may not conflict there. If it does,
the resolution is "keep `sl-closeout`'s corrected header, keep this item's
replacement of the `model_list` block with the pointer comment" — the two changes
are independent in meaning.

## F10 — `.lmstudio` still appears inside the inference plane, in container paths and one comment

The acceptance criterion reads "grep for `Users\` and `.lmstudio` across the
inference plane finds nothing". The host path is gone: `grep -rn 'Users' inference/`
returns nothing. What remains, deliberately:

- `inference/compose/upstreams.yml:66,75` —
  `/models/lmstudio-community/Qwen3.6-...-GGUF/...gguf`. These are paths **inside**
  the container, and `lmstudio-community` is the publisher's Hugging Face
  namespace, i.e. a directory name inside whatever `LM_MODELS_DIR` points at.
  Changing it would mean renaming directories in the operator's model store.
- `inference/compose/backups.yml:39` — the pre-existing comment "the .lmstudio
  path is historical", which is now more true, not less.

Read literally as a regex, `.lmstudio` matches those three lines. Read as "no
user's home path is baked into the plane", the criterion passes. Flagging the
ambiguity rather than deleting evidence to make a grep green.

## F11 — splitting a compose file out of `docker-compose*.yml` silently loses its LF rule

`.gitattributes` forces LF only on names matching `docker-compose*.yml`. The
moment a service group moves to `inference/compose/backups.yml`, that rule stops
applying and Git (`core.autocrlf=true` on this host) checks the file out as CRLF.

That is not cosmetic here: `lm-models-backup` carries a multi-line shell script
under `command:`, and a CRLF checkout hands `sh -c` a literal `\\r` on every line —
the same class of failure as the worktree-CRLF break that stopped a docker build
in 2026-08.

Fixed in this item by adding `*/compose/*.yml text eol=lf` next to the existing
compose rules, so the pattern covers every plane that splits the same way (the
stack-layers plan expects more to follow). Verified:
`git check-attr text eol -- inference/compose/backups.yml` goes from
`unspecified` to `text: set` / `eol: lf`.

**Anyone splitting another plane's compose file must check this**, and anyone
renaming the `compose/` subdirectory breaks it again.
