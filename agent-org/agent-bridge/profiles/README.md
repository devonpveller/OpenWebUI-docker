# Role/model profiles (C4, PLAN §5.4)

A **profile is the role primitive.** It binds `{lane, model, system_prompt_ref=charter,
temperature, tool_access=scope, caller_key}` to a role name. **Adding a role = adding a
profile** — never a gateway change (only a genuinely new *underlying model* touches a gateway
config). The bridge seeds the DB from these JSON files at boot; lane-flips persist in the DB
(a persisted operator flip is not clobbered by the seed).

## Lanes — the pre-P0.5 default is **local**

Every profile ships `lane: "local"`, on its TIER's model role (see "Tiers" below: `local-large`, the resident 27B, for every judgement role; `local-small` for the file-scoped worker - see `inference/README.md`). This is the honest, fail-safe
pre-decision posture: until the **P0.5 capability-floor test** decides whether local 27B
judgment is strong enough, *everything runs local on the same model* (zero swap thrash;
governance §2.1 "default everything local").

**If P0.5 mandates a cloud judge**, flip the judgment roles to the cloud lane — a one-field
edit, no code change (Pc.3 done-when):

```bash
# after Pc stands up llm-gateway-cloud (+ ao-egress + OpenRouter models):
curl -X POST http://agent-bridge:8000/profiles/lane -d '{"name":"pm","lane":"cloud"}'
curl -X POST http://agent-bridge:8000/profiles/lane -d '{"name":"po","lane":"cloud"}'
curl -X POST http://agent-bridge:8000/profiles/lane -d '{"name":"planner","lane":"cloud"}'
curl -X POST http://agent-bridge:8000/profiles/lane -d '{"name":"reviewer-ethics","lane":"cloud"}'
# workers ALWAYS stay local.
```

`AO_CLOUD_ENABLED=true` (+ `AO_CLOUD_API_BASE` / `AO_CLOUD_API_KEY`) must be set for the cloud
lane to actually route out; until then a `cloud`-lane call **falls back to local with a
warning** (never silently trusts a weak monitor — the Human Operator carries more, §2.1).

## The 8 seed profiles
| profile | lane | role | charter |
|---------|------|------|---------|
| `worker-default` | local | domain executor | `charters/worker-default.md` |
| `pm` | local (→cloud) | monitor/manager | `charters/pm.md` |
| `po` | local (→cloud) | overseer | `charters/po.md` |
| `planner` | local (→cloud) | plan generation | `charters/planner.md` |
| `reviewer-ethics` | local (→cloud) | whole-picture lens | `charters/reviewer-ethics.md` |
| `reviewer-correctness` | local (→cloud) | correctness lens | `charters/reviewer-correctness.md` |
| `reviewer-security` | local (→cloud) | security lens | `charters/reviewer-security.md` |
| `reviewer-scope` | local (→cloud) | scope-creep lens | `charters/reviewer-scope.md` |

⚠️ **Tune the local↔cloud boundary empirically** (operator): stretch local as `local-large`
proves capable; the cloud budget caps the rest (UX-FLOW §6).

## Tiers - which model SIZE a role's task kind deserves (mt-policy)

Operator, 2026-09-30: *planning the work requires long horizon and plan considerations while
the work to one file or another requires only their specific narrow scope of work.* So every
profile carries a `tier`, and the tier names its model role per lane
(`AO_PROFILE_TIER_MODELS_LOCAL`, default `large=local-large,small=local-small`;
`AO_PROFILE_TIER_MODELS_CLOUD`, default `large=cloud-large,small=cloud-small`):

| task kind | profile(s) | tier | local model role |
|---|---|---|---|
| planning, readiness | `planner` | large | `local-large` |
| decomposition, intent, lifecycle plan | `po` | large | `local-large` |
| monitoring: deviation, alignment, plan gate, triage | `pm` | large | `local-large` |
| review (every lens) | `reviewer-*` | large | `local-large` |
| operator-facing synthesis | `pm-voice` | large | `local-large` |
| file-scoped edits, mechanical steps | `worker-default` | small | `local-small` |

`pm-voice` stays large on purpose: what it writes is what the operator believes, and its
charter calls a confident wrong answer the worst thing it can produce - not a mechanical step.

The routing path is the one that already exists - a task kind runs under its profile and
`ModelRouter` asks the gateway for that profile's `model`. Nothing new routes; the tier only
says which model each profile SHOULD carry. `tier` is read from these seed files on every boot
(it is policy, not a database column); the live `model` still belongs to the database and
moves only by the governed intent below. `GET /profiles` reports `tier_drift`: every profile
whose live model is off its tier, each with the exact command that would move it - it changes
nothing itself. If `AO_PROFILE_TIER_MODELS_LOCAL`/`_CLOUD` is malformed or leaves a tier without a
model (e.g. `large=local-large`), the listing still answers 200 with `tier_drift: null` and a
`tier_drift_error` naming the variable, and the bridge logs it.

Known limit, stated rather than implied: the pooled little-coder WORKERS run the model in
little-coder's own config (`little-coder/config/little-coder.config.yaml`, `agent.model`); their
task API takes no model, so `worker-default`'s model is the org's recorded policy for that role
and is not yet what a worker dispatch runs on.

The ai-stack harness's tier policy for CLAUDE CODE subagents (opus / sonnet / haiku) is a
separate configuration - `scripts/agent-harness/harness.config.json` `model_tiers` - and is
deliberately not merged with this one.

## Changing a profile's model on a running install

These files only SEED a profile the database does not have yet; after that the database
owns the live values (so an operator's lane flip survives a restart). A new install seeds
`local-large`, the model role (`inference/README.md`). An existing install moves a profile
by saying so, like every operator inlet (NL -> OperatorIntent -> a governed handler):
`set profile <name> model <model>` in `#mgmt` or through `POST /nl` (`{"message": ...,
"actor": "<who>"}`), with `(dry run)` on the end to only check. Only that exact command
applies: a looser request the PO model reads as a profile change is always answered as a dry
run that quotes the exact command to send. The handler refuses an unknown profile (names are
case-folded; models are not), a model outside the CHAT set for the profile's lane
(`AO_PROFILE_CHAT_MODELS_LOCAL` / `_CLOUD`; embedding names and the cloud group are refused on a
local profile), a model the gateway does not list, and a gateway it cannot ask. It writes ONE new
profile version with only `model` changed (lane, charter, temperature, scope and caller key
carried over) and audits a `profile_model_set` event with who asked and the before/after. The
same model again writes nothing; a second request racing the first on one profile is refused
("changed concurrently; retry", HTTP 409 on `/nl`); the same command with the old model is the
rollback.
