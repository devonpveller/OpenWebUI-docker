# Role/model profiles (C4, PLAN §5.4)

A **profile is the role primitive.** It binds `{lane, model, system_prompt_ref=charter,
temperature, tool_access=scope, caller_key}` to a role name. **Adding a role = adding a
profile** — never a gateway change (only a genuinely new *underlying model* touches a gateway
config). The bridge seeds the DB from these JSON files at boot; lane-flips persist in the DB
(a persisted operator flip is not clobbered by the seed).

## Lanes — the pre-P0.5 default is **local**

Every profile ships `lane: "local"` (model `qwen36-27b`). This is the honest, fail-safe
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

⚠️ **Tune the local↔cloud boundary empirically** (operator): stretch local as `qwen36-27b`
proves capable; the cloud budget caps the rest (UX-FLOW §6).
