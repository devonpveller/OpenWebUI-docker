# sl-docs-posture - test plan (attempt 2)

**Item:** `sl-docs-posture` (stack-layers). **Branch:** `work/sl-docs-posture`,
cut from `development` at `a2d3644`. **Worktree:**
`.claude/worktrees/wt-sl-docs-posture`. **Anchor:**
`../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-docs-posture.json`.

**Attempt 1 FAILED at `f34b86e` on T1, T3 and T8, and the PLAN was ruled
inadequate** - its T3 and T8 pass criteria each asserted something measurably
false, so a tester following it literally would have blessed two wrong
sentences. What changed, and what you should therefore attack hardest:

- **The component set is now derived from the RENDERS**, not from a grep for
  provider names. T1 below is the method, not a checklist. Four unprofiled
  services were missing in attempt 1; if this method is right, a fifth should be
  findable the same way, and finding one is a FAIL.
- **T3's criterion is now a measured SET of egress containers**, because "the
  one egress container is search's `vpn`" was false and contradicted the
  artifact's own `lc-egress` row.
- **T8's README-to-CLAUDE.md mapping is re-derived** rather than asserted; two
  README rows had no home in attempt 1.
- **T9 is new**: every sentence added in attempt 2, separately, so the delta can
  be checked without re-reading the whole ledger.

**This is a documentation-only item.** The diff is Markdown and nothing else.
Discrepancies found in compose/config/scripts go to
`documentation/notes/stack-layers-sl-docs-posture-findings.md` rather than being
fixed - F1 and F7-F12 there are part of what you are checking, because the
shipped prose asserts them.

**Nothing in this plan starts, stops, restarts or recreates a container, builds
an image, or writes to any `ai-stack_*` network.** `docker compose config` is a
pure render. The only writes are JSON state files under a scratch path (T5) and
`.env`/recipe files inside a scratch CLONE. No plane lease is needed. Do not
print the contents of any real `.env`.

**Set up once:**

```bash
WT="D:/Open WebUI/ai-stack/.claude/worktrees/wt-sl-docs-posture"
COPY="$(mktemp -d)/ai-stack"
git clone --no-hardlinks "$WT" "$COPY"
cd "$COPY" && git checkout work/sl-docs-posture
for p in . frontend inference memory search coder portal agent-org/docker; do
  cp "$p/.env.example" "$p/.env"
done
SCRATCH="$(mktemp -d)"
```

Two notes the artifact now states and you should confirm are stated: `stack.py
enable` in the clone needs a non-blank `LITELLM_MASTER_KEY` and
`MULLVAD_WG_ADDRESSES` (placeholders only, never a real key), and **the OB1
plane cannot render in a fresh clone at all** until two empty
`OB1/recipes/*/.env` files exist. The clone's `OB1/` is an uninitialised
submodule, so run every OB1 render in `$WT`, where it is checked out at the
pinned `fe3e045`.

---

## T1 - acceptance 1: the posture table enumerates every component that can
## reach a non-local endpoint - DERIVE IT, do not check it

**The claim under test** (README, "How this list was built"): the table is the
output of a two-stage derivation, and it is complete.

> 1. Render every plane, every profile; a service is a *candidate* if any
>    network it joins is non-internal, with `external:` names resolved against
>    the anchor (`ai-stack_llm-net` internal; `ai-stack_app-net` and
>    `ai-stack_default` bridges).
> 2. Read each candidate's source for an actual outbound call.

**Do the derivation yourself, from scratch.** Do not start from the table.

```bash
cd "$COPY"   # (and "$WT" for the OB1 render)
for spec in "frontend/docker-compose.yml:stock" \
            "frontend/docker-compose.yml:gpu,tailscale" \
            "inference/docker-compose.yml:local" \
            "memory/docker-compose.yml:" \
            "search/docker-compose.yml:" \
            "coder/docker-compose.yml:" \
            "portal/docker-compose.yml:internet" \
            "agent-org/docker/docker-compose.yml:workers,cloud"; do
  f="${spec%%:*}"; profs="${spec#*:}"
  args=""; for p in $(echo "$profs" | tr ',' ' '); do args="$args --profile $p"; done
  echo "== $f [$profs]"
  docker compose -f "$f" $args config --format json
done
# OB1, in $WT:
docker compose -f OB1/docker/docker-compose.yml \
  --profile research --profile wiki --profile notebook --profile idea-refinery \
  config --format json
```

Note `frontend` must be rendered TWICE - `stock` and `gpu,tailscale` in one
render collide on the container name `openwebui` and the render exits non-zero.

For each render, read `services.<name>.networks` against the render's own
top-level `networks` block, apply the anchor resolution, and produce your own
two lists: **services with no non-internal network** (confined) and
**candidates**. Then open the source of every candidate and decide whether it
makes a call off this host.

**Pass:** your candidate list and your stage-2 verdicts agree with the
artifact's table plus its "network-capable, no outbound call" list, with no
service unaccounted for. The eighteen table rows and the reading that settles
each:

| # | Row | Settled by reading |
|---|---|---|
| 1 | LiteLLM `cloud-large`/`cloud-small` | `inference/config/litellm/model_list/cloud.openrouter.yaml` |
| 2 | agent-org `llm-gateway-cloud`, `-db`, `ao-egress` | `agent-org/docker/docker-compose.yml` - `profiles: ["cloud"]` on all three |
| 3 | `ao-git-egress` + the worker pool | same file - `profiles: ["workers"]`; and the render's `environment` for each of the four pool services (only `ao-ot-*` carry `HTTP_PROXY`) |
| 4 | agent-bridge GitHub App | `agent-org/agent-bridge/app/config.py` |
| 5 | `lc-egress` + `open-terminal` | `coder/docker-compose.yml` - no `profiles:` key on `lc-egress` |
| 6 | search `vpn` | `search/docker-compose.yml`; `search/searxng/settings.yml` |
| 7 | `cloudflared` | `portal/docker-compose.yml`; `scripts/portal/portal-on.ps1` |
| 8 | `portal-alerter` | same file, incl. the whole `networks:` block - count the non-internal ones yourself |
| 9 | `tailscale` | `frontend/docker-compose.yml` |
| 10 | `openwebui` (either profile) | same file - the two definitions' `networks:`, and `SEARXNG_QUERY_URL` |
| 11 | `openwebui-backup` | same file - its `command:` block |
| 12 | `mnemory-cloud-gateway` | `memory/docker-compose.yml`; `memory/mnemory-gateway/app.py` |
| 13 | `openbrain-gateway` + `-ops-gateway` | `OB1/docker/docker-compose.yml`; `openbrain-gateway/app.py` incl. its `WRITE_TOOLS` default |
| 14 | `openbrain-mcp` | `OB1/integrations/kubernetes-deployment/index.ts` - `ingestOne`, and a `proxy` search over the whole file |
| 15 | `openbrain-grounding-backfiller` | `OB1/integrations/grounding-backfiller/index.ts` - `WIKI_BASE`, `REFETCH_ALLOW_DIRECT`, `refetchOne` |
| 16 | `openbrain-wiki`'s `WIKI_GIT_REMOTE` | `OB1/docker/docker-compose.yml` |
| 17 | OB1's scheduled chain | `OB1/docker/docker-compose.scheduled.yml` - the `secrets/google/...` mounts |
| 18 | `openbrain-research` | `OB1/docker/docker-compose.yml` - `FETCH_PROXY_URL`, `SEARCH_API_BASE` |

The artifact ALSO claims a "network-capable, no outbound call" set and a
"host-side, not compose" set. **Re-decide every one of these yourself** - the
artifact's reason is given so you can refute it, not so you can accept it:

| Candidate | The artifact's reason for excluding / for host-side |
|---|---|
| `llm-gateway-ui` | on `app-net`, but `inference/config/litellm.ui.config.yaml` has no `model_list`, `telemetry: false`, `LITELLM_LOCAL_MODEL_COST_MAP=True` |
| search `gateway` | its only HTTP client targets `searxng` |
| `openbrain-ext` | its one outbound `fetch` is `WIKI_RECOMPILE_URL`, internal |
| `caddy` | every site address in the `Caddyfile` is `http://` or a bare port, so no ACME |
| `authelia` | notifier is `filesystem`; on `auth-net` (`internal: true`) |
| portal watchers, tripwire, `portal-cron` | internal targets only |
| `status-pipe/` | every request target is an internal `host:port` |
| backup sidecars other than `openwebui-backup` | each runs only its own script; the NAS sync is SMB to a LAN address |
| `mattermost` | FLAGGED, not cleared - no `MM_LOGSETTINGS_ENABLEDIAGNOSTICS` here, upstream default unverifiable from this tree |
| sysadmin Telegram notifier + listener | host process, not a container |
| claude-sessions bridge | host process; `BRIDGE_MODEL` defaults to `opus` |
| `owui/` plugins | deploy-by-paste, live in the OWUI database |

**Fail if:** you find a service on a non-internal network whose source makes an
outbound call and which appears in neither the table nor the excluded list; a
table row names a service, file or variable that does not exist; an excluded
row's stated reason is wrong; or the derivation as written does not reproduce
the artifact's split.

---

## T2 - acceptance 2a: for every listed component, the enabling variable and
## file are correct

(Passed at attempt 1; re-run, because rows 10, 11 and 14-17 are new.)

```bash
cd "$COPY"
grep -n 'OPENROUTER_API_KEY' inference/compose/gateway.yml
python - <<'PY'
import os, subprocess, sys, tempfile, pathlib
src = pathlib.Path("inference/config/litellm")
out = pathlib.Path(tempfile.mkdtemp())
base = out / "base.yaml"; base.write_text("general_settings: {}\n")
env = dict(os.environ, LITELLM_BASE_CONFIG=str(base),
           LITELLM_FRAGMENT_DIR=str(src / "model_list"),
           LITELLM_EFFECTIVE_CONFIG=str(out / "eff.yaml"),
           COMPOSE_PROFILES="", OPENROUTER_API_KEY="")
for label in ("key BLANK", "key SET"):
    r = subprocess.run([sys.executable, str(src / "assemble-config.py")], env=env,
                       capture_output=True, text=True)
    print("===", label, "===\n", r.stderr)
    env["OPENROUTER_API_KEY"] = "sk-not-a-real-key"
PY
```

**Pass:** blank key gives
`DROP cloud-large (cloud.openrouter.yaml) - env not set: OPENROUTER_API_KEY`
(and the same for `cloud-small`), `LOAD cloud.openrouter.yaml - 0 model(s)`,
`SKIP local.yaml - needs compose profile 'local'`, and `wrote … with 0
model(s)`; key set gives `LOAD cloud.openrouter.yaml - 2 model(s) registered`.
The DROP string must match `inference/README.md`'s quotation character for
character.

**Do NOT settle the `x-requires-profile` claim by grep** - `cloud.openrouter.yaml`
contains that string in a COMMENT ("There is no `x-requires-profile` here on
purpose"). Attempt 1's plan proposed that grep; the behavioural run above is
what settles it, and it settles it in the artifact's favour.

```bash
docker compose -f agent-org/docker/docker-compose.yml config --services | sort
docker compose -f agent-org/docker/docker-compose.yml --profile cloud config --services | sort
docker compose -f agent-org/docker/docker-compose.yml --profile workers config --services | sort
docker compose -f portal/docker-compose.yml config --services | grep -c cloudflared
docker compose -f portal/docker-compose.yml --profile internet config --services | grep -c cloudflared
docker compose -f frontend/docker-compose.yml config --services | sort
docker compose -f frontend/docker-compose.yml --profile gpu --profile tailscale config --services | sort
sed -n '/github_app_enabled/,/return bool/p' agent-org/agent-bridge/app/config.py
```

**Pass:** `+cloud` adds exactly `ao-egress, llm-gateway-cloud,
llm-gateway-cloud-db`; `+workers` adds exactly `ao-git-egress, ao-ot-1, ao-ot-2,
ao-worker-1, ao-worker-2` and the two journal backups; `cloudflared` 0 bare and
1 with `--profile internet`; the frontend bare render is exactly
`openwebui-stock` + `openwebui-backup`, and `gpu,tailscale` gives `openwebui`,
`tailscale`, `tailscale-backup`, `openwebui-backup`; `github_app_enabled`
returns `bool(self.github_app_id) and os.path.isfile(...)`.

---

## T3 - acceptance 2b: "off by default" - the MEASURED egress set

Attempt 1 failed here on an absolute. The claim is now a table of three, and it
is the thing to attack.

```bash
cd "$COPY"
for f in .env frontend/.env inference/.env memory/.env search/.env coder/.env portal/.env agent-org/docker/.env; do
  echo "== $f"
  grep -nE 'OPENROUTER_API_KEY|CLOUDFLARE_TUNNEL_TOKEN|AO_CLOUD_|TAILSCALE_AUTH_KEY|MULLVAD_WG_PRIVATE_KEY|LC_DEPLOY_TOKEN|LC_SELF_REMOTE_PAT|AO_GITHUB_APP|COMPOSE_PROFILES' "$f"
done
for p in frontend inference memory search coder portal; do
  echo "== $p"; docker compose -f $p/docker-compose.yml config --services | sort
done
docker compose -f agent-org/docker/docker-compose.yml config --services | sort
```

**Pass, in three parts:**

1. **Credentials.** Every PROVIDER credential is blank (`OPENROUTER_API_KEY`,
   `CLOUDFLARE_TUNNEL_TOKEN`, `AO_CLOUD_API_KEY`, `LC_DEPLOY_TOKEN`,
   `LC_SELF_REMOTE_PAT`) or an unmistakable placeholder
   (`TAILSCALE_AUTH_KEY=putyourtskeyhere`,
   `MULLVAD_WG_PRIVATE_KEY=change-me-real-wg-private-key`). The artifact also
   now names `AO_CLOUD_DB_PASSWORD=change-me-cloud-db` and
   `AO_CLOUD_MASTER_KEY=change-me-cloud-master` as placeholders that are NOT
   provider credentials - check it says that, since omitting them made the
   original list read as exhaustive when it was not. `AO_GITHUB_APP*` appears
   nowhere.
2. **Profiles.** The only ACTIVE `COMPOSE_PROFILES` assignment in any example is
   `frontend/.env.example`'s `COMPOSE_PROFILES=stock`. Note the artifact now
   says "active": `inference`'s is commented out, and a commented
   `gpu,tailscale` line sits a few lines above the active one in
   `frontend/.env.example` - an absolute "no `COMPOSE_PROFILES` line anywhere
   names `tailscale`" would be false, so check the wording, not just the fact.
3. **The egress set.** Exactly three containers render-and-start from the
   examples with a route off the host that they use or hold:

| Container | Plane | Claim |
|---|---|---|
| `vpn` | search | unprofiled; dials Mullvad itself at start; cannot connect on the placeholder key |
| `openwebui-backup` | frontend | unprofiled, renders under `stock` (so it is in the QUICKSTART), `command:` begins `apk add --no-cache pigz` |
| `lc-egress` | coder | unprofiled, dual-homed; initiates nothing itself but is `open-terminal`'s only route |

**Fail if:** a fourth container belongs in that table (check every bare render's
service list against your T1 candidate list - the backup sidecars especially:
the artifact claims `openwebui-backup` is the ONLY runtime package install in
any compose file, and `git grep -n 'apk add' -- '*docker-compose*.yml'` settles
it); or any of the three claims is wrong; or the prose anywhere reverts to "the
one egress container".

---

## T4 - acceptance 3: the D14 mechanism sentence

(Passed at attempt 1. Re-run only the attribution check, which is the subtle
part, plus the new `inference/README.md` paragraph.)

```bash
cd "$WT"
docker compose -f inference/docker-compose.yml config --format json \
  | python -c "import json,sys; d=json.load(sys.stdin); print({s: sorted((v.get('networks') or {}).keys()) for s,v in d['services'].items()})"
grep -n -A2 'llm-backend-net:' inference/docker-compose.yml
grep -n -A2 '  llm-net:' docker-compose.yml
```

**Pass:** `llm-gateway` has exactly `llm-backend-net` + `llm-net`;
`llm-backend-net` is `internal: true` in `inference/docker-compose.yml`;
`llm-net` is `internal: true` in the ANCHOR's root file. All three documents
attribute each declaration to the right file - look specifically for the wrong
version ("both declared internal in inference/docker-compose.yml"), which would
be a FAIL. All three say what an egress path would take and say not to add one;
`git diff` adds no compose or config change.

**New in attempt 2:** `inference/README.md` now opens its posture section with
the render-derived classification for this plane - seven of eight services
confined, `llm-gateway-ui` on `app-net` as a route with no call - before
claiming "exactly one cloud-capable component". Check that claim survives the
classification rather than sitting beside it.

---

## T5 - acceptance 4: no document tells a reader to run `init --force` on an
## existing host

(Passed at attempt 1 and unchanged in attempt 2. Re-run the greps to confirm
nothing regressed, and re-measure rather than reading the numbers off the doc.)

```bash
cd "$WT"
git grep -n 'init --force'
git grep -nE 'init.*--product'
for p in frontend inference memory search coder agent-org; do
  python scripts/stack/stack.py --state "$SCRATCH/a.json" enable "$p" >/dev/null
done
cp "$SCRATCH/a.json" "$SCRATCH/b.json"
python scripts/stack/stack.py --state "$SCRATCH/a.json" init --product research --force
python scripts/stack/stack.py --state "$SCRATCH/b.json" enable research
python scripts/stack/stack.py --state "$SCRATCH/b.json" init --product research; echo "exit=$?"
python -c "import json;print(sorted(json.load(open(r'$SCRATCH/a.json'))['planes']))"
python -c "import json;print(sorted(json.load(open(r'$SCRATCH/b.json'))['planes']))"
```

**Pass:** six planes in; `init --force` leaves FOUR
(`frontend, inference, ob1, search`); `enable research` leaves SEVEN; both print
the same `enabled product research:` block; the no-`--force` run refuses with
`already exists (re-run with --force to overwrite it)`, exit 1. Every Markdown
survivor of the two greps is the fresh/scratch case, a quotation of driver
output, or explains the replacement; the four non-Markdown survivors (F2) are
not in the diff. The live `.stack/state.json` is untouched and still lists seven
planes.

---

## T6 - the defect claims the prose ships (F1, F7, F8)

F1 passed at attempt 1. F7 and F8 are new and the prose asserts them, so a wrong
one ships as a wrong sentence.

**F1 - `ao-egress`'s `EGRESS_ALLOWLIST` is inert.** `Dockerfile.egress` has no
`ENTRYPOINT`, its `CMD` runs tinyproxy against the COPYd allowlist,
`tinyproxy.conf` is `FilterDefaultDeny Yes`, the allowlist is the two GitHub
patterns, `ao-egress` overrides no `command`/`entrypoint`/volume, and a tree-wide
grep for `EGRESS_ALLOWLIST` finds only declarations and documentation - no
reader. `ao-git-egress` overrides both the conf and the command.

**F7 - `openbrain-mcp` fetches a caller-supplied URL unproxied.**

```bash
cd "$WT"
grep -n 'await fetch(url' -A4 OB1/integrations/kubernetes-deployment/index.ts
grep -c -i 'proxy' OB1/integrations/kubernetes-deployment/index.ts     # expect 0
grep -n 'WRITE_TOOLS' openbrain-gateway/app.py
docker compose -f OB1/docker/docker-compose.yml config --format json \
  | python -c "import json,sys; d=json.load(sys.stdin); print(d['services']['openbrain-mcp'].get('profiles'), sorted(d['services']['openbrain-mcp']['networks']))"
```

**Pass:** the bare `fetch(url)` with `redirect: "follow"` exists inside
`ingestOne`; the proxy grep returns 0; `WRITE_TOOLS` defaults include
`ingest_url` and `ingest_urls`; `openbrain-mcp` has no profile and is on
`obnet` + `llm-net`. **Fail:** a proxy client exists in that file (then the
README's `openbrain-mcp` row and the reworded cloud-door row are wrong), or the
tools are not in the door's default allowlist.

**F8 - the backfiller's direct fallback defaults on.**

```bash
grep -n 'WIKI_BASE\|REFETCH_ALLOW_DIRECT' OB1/integrations/grounding-backfiller/index.ts
grep -n 'direct fallback' -B3 OB1/integrations/grounding-backfiller/index.ts
```

**Pass:** `WIKI_BASE` defaults to `https://en.wikipedia.org`,
`REFETCH_ALLOW_DIRECT` defaults to `"true"`, and `refetchOne` calls
`fetchExtract(src.url, false)` - the unproxied form - when the proxied result is
thin. The service is unprofiled. **Fail:** the default is `false`, or the
fallback is also proxied - then the README's row and its warning against
generalising row 17 are wrong.

---

## T7 - gates, and the diff is Markdown only

```bash
cd "$WT"
git diff --name-only development...HEAD | grep -v '\.md$'      # expect NO output
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/plan-store.ps1 -Store "D:\Open WebUI\documentation-plans-ai-stack"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-doc-placement.ps1 -All
ruff check .
git log --format='%b' development..HEAD | grep -c 'Co-Authored-By: Claude'
```

**Pass:** no non-Markdown file in the diff; both checks exit 0; `ruff` clean;
every commit carries the trailer and the hook attestation covers every commit.

---

## T8 - the claim ledger, re-derived

Every sentence the item introduced, and what settles it. **A sentence in the
diff that is not in this table or in T9 is a FAIL in itself.**

### `README.md` - the method and the table

| Sentence / cell | Settled by |
|---|---|
| "No component in this stack sends a prompt, a document or a memory to a model provider by default." | T1 (the set is complete) + T3 (no provider credential set) + the host-side paragraph, which is why the sentence says COMPONENT |
| "That is not the same as 'nothing reaches the internet'. Three containers do, from a fresh clone" | T3 part 3 |
| the two-stage method, incl. the anchor resolution of `external:` names | T1 - the derivation either reproduces the split or it does not |
| "A grep for provider names finds stage 2 and misses stage 1 entirely - which is how the first version of this section missed four services, every one of them unprofiled." | the attempt-1 evidence at `C:\tdp\sl-docs-posture-evidence-a1.md` (M1-M4), and T1 |
| Row 1 (assemble-config drops, `OPENROUTER_API_KEY`, egress "Nowhere") | T2 + T4 |
| Row 2 (three services, `profiles: ["cloud"]`, no `COMPOSE_PROFILES` in the example, `AO_CLOUD_ENABLED=false`, the proxy topology) | T2 + T3 |
| Row 3 - **the split mechanism**: only `ao-ot-*` carry `HTTP_PROXY`; `ao-worker-*` carry none and are confined by `ao-worker-net` | the render's `environment` for all four pool services, and the `networks:` block |
| Row 4 (GitHub App gate, absent from the example by convention, `api.github.com` off `ao-net` not via `ao-egress`) | T2; `grep -c AO_GITHUB agent-org/docker/.env.example` -> 0; the rendered `agent-bridge` has no `HTTP_PROXY` |
| Row 5 (`lc-egress` unprofiled, default-deny, baked allowlist, CONNECT 443/22, `open-terminal` has no other route) | T1 row 5 + T6/F1 + `coder/docker-compose.yml` |
| Row 6 (Mullvad, unprofiled, placeholder key, `searxng` internal + `outgoing.proxies`) | T3 + `search/searxng/settings.yml` |
| Row 7 (`internet` profile, `manual` plane, `portal-on.ps1`, `caddy` publishes no host port) | T2 + `stack.manifest.toml` |
| Row 8 - **FOUR non-internal portal networks** (`edge-net`, `notify-net`, external `app-net`, implicit `default`), and `caddy` on two of them | the portal render's `networks` block and each service's `networks` - count them; attempt 1 said "exactly two" and was wrong |
| Row 9 (`tailscale` profile needs `gpu` because `network_mode: service:openwebui`) | `frontend/docker-compose.yml` |
| Row 10 - `openwebui` under EITHER profile is network-capable; `SEARXNG_QUERY_URL` bounds the search QUERY only, and no `HF_HUB_OFFLINE` is set | the two definitions' `networks:`; `grep -c HF_HUB_OFFLINE frontend/docker-compose.yml` -> 0. The row deliberately does NOT claim OWUI does or does not fetch at runtime |
| Row 11 - `openwebui-backup` is unprofiled, renders under `stock`, `apk add --no-cache pigz` at every start, and is the only runtime package install in any compose file | its `command:` block; the compose comment beside its `networks:`; `git grep -n 'apk add' -- '*docker-compose*.yml'` |
| Row 12 - `mnemory-cloud-gateway`'s only upstream is `MNEMORY_URL`; loopback `:8060` | `memory/mnemory-gateway/app.py`; the `ports:` line |
| Row 13 - the two OB1 doors, their differing filters and keys, AND "not simply a door in" because the cloud door's default `WRITE_TOOLS` includes `ingest_url`/`ingest_urls` | T6/F7; `openbrain-gateway/app.py`; the `${OPS_GATEWAY_KEY:?…}` guard |
| Row 14 - `openbrain-mcp`: nothing gates it, bare `fetch(url)` with redirects followed, no proxy client in that file, reachable through the cloud door | T6/F7 |
| Row 15 - the backfiller: unprofiled, `WIKI_BASE` Wikipedia, `REFETCH_ALLOW_DIRECT` defaults true, fails OPEN, closed by `REFETCH_ALLOW_DIRECT=false` | T6/F8 |
| Row 16 - `WIKI_GIT_REMOTE: ""` with the SSH URL commented beneath; restoring it force-pushes the vault | `OB1/docker/docker-compose.yml` |
| Row 17 - the scheduled chain's OAuth mounts and `wttr.in` | `OB1/docker/docker-compose.scheduled.yml` |
| Row 18 - `openbrain-research` is the ONE proxy-bound OB1 fetcher, "do not generalise" | `FETCH_PROXY_URL` on `openbrain-research`; contrast with rows 14 and 15 |
| "Network-capable, no outbound call" - each of the eight entries, incl. `mattermost` as FLAGGED | T1's second table; each entry's own reason |
| "Host-side, not compose" - the three entries | T1's second table; `scripts/sysadmin-mcp/telegram_{notify,listener}.py`, `scripts/claude-sessions-bridge/bridge.py`, `owui/tools/*.py` + `owui/manifest.csv` |
| "The D14 mechanism, exactly." | T4 |
| "Before you enable the agent-org cloud lane, read its allowlist." | T6/F1 |
| "What a fresh clone actually does" - the credential list, the "active assignment" wording, the three-container table | T3 |

### `CLAUDE.md` - the posture paragraph

**The mapping is the test.** Attempt 1's list had no home for two README rows.
Build the mapping yourself from the two texts; do not read it off this table.

| README row | CLAUDE.md item |
|---|---|
| 1 LiteLLM cloud group | (1) |
| 2 agent-org cloud lane | (2) |
| 3 `ao-git-egress` + pool | (3) |
| 4 GitHub App | (4) |
| 5 `lc-egress` | (5) |
| 6 Mullvad `vpn` | (6) |
| 7 `cloudflared` / 8 `portal-alerter` | (7) |
| 9 `tailscale` | (8) |
| 10 `openwebui` / 11 `openwebui-backup` | (9) |
| 12 `mnemory-cloud-gateway` / 13 the OB1 doors | (10) |
| 14 `openbrain-mcp` | (11) |
| 15 backfiller | (12) |
| 16 `WIKI_GIT_REMOTE` | (13) |
| 17 scheduled chain | (14) |
| 18 `openbrain-research` | (15) |
| "Host-side, not compose" | the HOST-SIDE sentence |
| "Network-capable, no outbound call" | not carried, deliberately - the agent-facing paragraph lists what IS capable; check the README carries it |

**Fail if** any README row has no home, or a CLAUDE.md item names something with
no README row. Also check the paragraph still answers, with no compose file
open: *what is cloud-capable*, *what turns each on*, *what egresses by default*.

### `inference/README.md`, `agent-org/README.md`, `scripts/stack/README.md`, and the init/enable surfaces

| Sentence | Settled by |
|---|---|
| `inference/README.md`'s render-derived opening (seven of eight confined; `llm-gateway-ui` a route with no call) | T4's render + `inference/config/litellm.ui.config.yaml` |
| "exactly one cloud-capable component" | T1 restricted to `inference/` |
| the two admission rules, the exact DROP log line, the "LISTED not REACHABLE" paragraph, the D11 rationale | T2 + T4 |
| `agent-org/README.md`'s four rows | T1 rows 2, 3, 4 |
| its Row 3 correction - "confined two DIFFERENT ways, so do not say 'proxied through it' of all four" | the render's per-service `environment` |
| its `ao-egress` allowlist paragraph | T6/F1 |
| its `mattermost` FLAG | T1's excluded table |
| `scripts/stack/README.md`'s `init`/`enable` paragraph and table | T5 |
| the five init/enable surfaces + the two findings-note corrections | T5 |

---

## T9 - the attempt-2 delta: every sentence added THIS round

Checkable on its own with `git diff f34b86e..HEAD`.

| Added in attempt 2 | Settled by |
|---|---|
| README: the "That is not the same as 'nothing reaches the internet'" sentence | T3 |
| README: the whole "How this list was built" block (two stages, anchor resolution, the grep critique) | T1 |
| README: rows 10, 11, 14, 15, 16 (new) | T1, T3, T6 |
| README: row 13's "not simply a door in" clause | T6/F7 |
| README: row 18's "**this one** … do not generalise" clause | T6/F8 + F7 |
| README: row 3's split-mechanism clause | the render |
| README: row 8's FOUR-networks correction | the portal render |
| README: the "Network-capable, no outbound call" section (eight entries) | T1's second table |
| README: the "Host-side, not compose" section (three entries) | T1's second table |
| README: "What a fresh clone actually does" - the `AO_CLOUD_*` placeholder clause, the "active assignment" wording, the three-container table | T3 |
| README: the OB1-cannot-render-in-a-fresh-clone clause in row 17 | render OB1 in a fresh clone and watch it refuse until two empty `OB1/recipes/*/.env` exist |
| CLAUDE.md: the two-stage method sentence, items (9), (11), (12), (13), (15), the (3) proxy-split clause, the (7) four-networks clause, the (10) `WRITE_TOOLS` clause, the HOST-SIDE sentence, the three-container closing sentence | T8's mapping + the same sources as the README rows |
| `inference/README.md`: the render-derived opening paragraph | T4 |
| `agent-org/README.md`: the row-3 correction and the `mattermost` FLAG | the render; T1 |
| findings note: F6 rewritten; F7-F12 added; the attempt-1-failed header | T3, T6, T1 |

**Fail if** a sentence appears in `git diff f34b86e..HEAD` that is in neither
this table nor T8.

---

## What is NOT in this item

- No compose, config, script, `.env.example` or source change. **F1, F7 and F8
  are NOT fixed** - the agent-org cloud lane still cannot reach openrouter.ai,
  `openbrain-mcp` still fetches arbitrary URLs unproxied, and the backfiller
  still falls back to direct.
- The `init --force` strings in `scripts/stack/stack.py`, `stack.ps1`,
  `stack.manifest.toml` and `test_stack.py` (F2).
- `OB1`'s own README (`sl-ob1-docs` owns it) and the OB1 gitlink.
- Any egress path, any key, any rotation (D2, operator).
