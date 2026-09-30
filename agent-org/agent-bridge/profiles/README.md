# Role/model profiles (C4, PLAN §5.4)

A **profile is the role primitive.** It binds `{lane, model, system_prompt_ref=charter,
temperature, tool_access=scope, caller_key}` to a role name. **Adding a role = adding a
profile** — never a gateway change (only a genuinely new *underlying model* touches a gateway
config). The bridge seeds the DB from these JSON files at boot; lane-flips persist in the DB
(a persisted operator flip is not clobbered by the seed).

## Lanes — the pre-P0.5 default is **local**

Every profile ships `lane: "local"` (model `local-large`, the resident 27B - see `inference/README.md`). This is the honest, fail-safe
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
