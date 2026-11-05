# Open Brain per-user MCP identity: Open WebUI connections, keys, deploy, rollback and rotation

This runbook covers how Open WebUI's Open Brain tools act as the signed-in user (research-workbench `rw-mcp-identity`):
what to configure, the pre-checks, how to switch Open WebUI's connections over, how to roll back, and how to rotate
each credential.

The code is `OB1/integrations/kubernetes-deployment/mcp-identity.ts`. Byte-identical copies live in
`OB1/docker/extensions-server/` and `OB1/docker/workbench/src/identity/mcp_identity.ts`. The deploy and the rollback
are rehearsed by `OB1/integrations/kubernetes-deployment/mcp-identity-lab/run-mcp-identity-deploy-lab.ps1`.

## How it works

| Lane (key in `x-brain-key`) | Who holds it | Acts as |
|---|---|---|
| `MCP_ACCESS_KEY` (full) | openbrain-gateway (Claude Code / Desktop), agent-bridge, OB1 services | openbrain-mcp: **the operator** (`WORKSPACE_DEFAULT_SUB`). openbrain-ext: **no identity** |
| `MCP_PERSONAL_ACCESS_KEY` (personal) | openbrain-mcpo, and openbrain-mcpo-ext after the F6 step | **no identity** (mcpo carries no user): shared rows only, every write refused |
| `MCP_OWUI_ACCESS_KEY` (owui) | Open WebUI's native MCP connections only (webui.db) | **the signed-in user**: the `sub` of their Authentik access token (`auth_type: system_oauth`). No token = no identity |

**The token check.** A token is accepted only when all of these hold. Any failure is **401**, never a fallback to "no
identity".
- Its RS256 / ES256 signature verifies against `MCP_OWUI_OIDC_JWKS`.
- `iss` equals `MCP_OWUI_OIDC_ISSUER`.
- `aud` holds `MCP_OWUI_OIDC_AUDIENCE`, and `azp`, when present, equals it.
- It has not expired (60 s leeway).
- Its `sub` is a usable subject.

**What else is refused (403):**
- Any other way of naming a user, on every lane: `x-on-behalf-of`, `X-OpenWebUI-User-*`, `x-authentik-*`, `x-ob-user*`,
  `x-user-id`, `remote-user`, `x-forwarded-user`, `x-remote-user`.
- An `Authorization` header on the full or personal lane.
- Two DIFFERENT keys on one request (the header, `x-access-key` or `?key=`).

**No identity:** reads return shared rows only (`user_id IS NULL`), and every write is refused, shared-knowledge writes
included.

**openbrain-ext rows.** The operator keeps `DEFAULT_USER_ID`'s rows. Every other user gets their own rows (`user_id`
is a UUIDv5 of their subject). Every id a write takes must name the caller's own row, and every nested join is
owner-scoped.

**The workbench (the contract tools).** openbrain-mcp's contract tools tell the workbench who the CALLER is, never this
server's own standing.

| Caller | What openbrain-mcp sends the workbench | What the workbench serves |
|---|---|---|
| Full lane | `MCP_ACCESS_KEY` (= `WORKBENCH_KEY`) | the operator, as before |
| Every other lane | its own service key `OB_WORKBENCH_MCP_KEY` (the workbench's `mcp=` entry in `WORKSPACE_SERVICE_KEYS`) | `service:mcp`, which is nobody: shared material only |
| A token-verified Open WebUI user | the same service key, plus that user's token in `x-ob-user-token` | that token's `sub`, after the workbench verifies the token itself against the same `MCP_OWUI_OIDC_*` |

- The workbench accepts `x-ob-user-token` only from the `mcp` key, and only on the contract reads. Misuse is 403; a bad
  token is 401.
- **Trust:** the key alone names nobody. A subject needs a live token that Authentik signed for that user, so anything
  holding the mcp key cannot name a user whose token it does not hold.

## Configuration (OB1/docker/.env; names only here)

| Variable | What it is |
|---|---|
| `WORKSPACE_DEFAULT_SUB` | the operator's real Authentik subject, never the word `operator` |
| `MCP_OWUI_ACCESS_KEY` | the OWUI lane key |
| `MCP_OWUI_OIDC_ISSUER`, `MCP_OWUI_OIDC_AUDIENCE`, `MCP_OWUI_OIDC_JWKS` | the provider; the workbench reads these three too |
| `OB_WORKBENCH_MCP_KEY` | openbrain-mcp's workbench key; the same value as the `mcp=` entry in `WORKSPACE_SERVICE_KEYS` |

Set all four `MCP_OWUI_*`, or none (the lane is then off). openbrain-mcp and openbrain-ext refuse to start on any of
these: a half-set lane, a reused key, or an OWUI lane without `WORKSPACE_DEFAULT_SUB`. Without
`OB_WORKBENCH_MCP_KEY`, the contract tools answer the full lane only.

### Create the two keys (written to the .env, never printed)

From the repo root, in PowerShell. Each command replaces or adds one line, and prints nothing:

```powershell
function New-HexKey { $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ($b | ForEach-Object { $_.ToString('x2') }) -join '' }
$f = (Resolve-Path 'OB1\docker\.env').Path
$k1 = New-HexKey; $k2 = New-HexKey
$lines = @(Get-Content $f | Where-Object { $_ -notmatch '^(MCP_OWUI_ACCESS_KEY|OB_WORKBENCH_MCP_KEY)=' }) + "MCP_OWUI_ACCESS_KEY=$k1" + "OB_WORKBENCH_MCP_KEY=$k2"
# the workbench's mcp= entry: appended to WORKSPACE_SERVICE_KEYS (an existing mcp= entry is replaced)
$lines = $lines | ForEach-Object {
  if ($_ -match '^WORKSPACE_SERVICE_KEYS=(.*)$') {
    $rest = @($Matches[1] -split ';' | Where-Object { $_ -and $_ -notmatch '^mcp=' })
    'WORKSPACE_SERVICE_KEYS=' + (($rest + "mcp=$k2") -join ';')
  } else { $_ }
}
if (-not ($lines -match '^WORKSPACE_SERVICE_KEYS=')) { $lines += "WORKSPACE_SERVICE_KEYS=mcp=$k2" }
[IO.File]::WriteAllLines($f, $lines)
Remove-Variable k1, k2
```

To paste the lane key into Open WebUI, put it on the clipboard without printing it, then clear the clipboard after:
- `(Select-String -Path OB1\docker\.env -Pattern '^MCP_OWUI_ACCESS_KEY=(.+)$').Matches[0].Groups[1].Value | Set-Clipboard`
- then `Set-Clipboard -Value ' '`.

### Issuer, audience, JWKS (public values)

- **Issuer:** the `issuer` of `<OPENID_PROVIDER_URL>` (frontend/.env), copied exactly, trailing slash included.
- **Audience:** Open WebUI's `OAUTH_CLIENT_ID` (frontend/.env).
- **JWKS:** the provider's `jwks_uri` from the same discovery document. Write it on one line, single-quoted:
  `MCP_OWUI_OIDC_JWKS='{"keys":[...]}'`.
- The JWKS is static on purpose: openbrain-mcp, openbrain-ext and the workbench fetch nothing, so they have no egress.

## Pre-checks (before the deploy window; each one: what to do, and what to do if it fails)

1. **The provider signs with a key (RS256 / ES256), not the client secret.** In Authentik, open Providers, then the Open
   WebUI provider, and check that "Signing Key" names a certificate. Fetch the `jwks_uri`: `keys` must hold an RSA or
   EC P-256 key.
   - If it fails: select a signing key (e.g. the self-signed certificate) and save. Then sign in to Open WebUI once to
     confirm SSO still works. Do not deploy until the JWKS has a key.
2. **The provider has NO Encryption Key.** In the same provider, "Encryption Key" must be empty. With one set, Authentik
   issues JWE access tokens (5 dot-separated parts), and every Open WebUI MCP request would be 401.
   - If it is set: stop. Clearing it changes what every client of this provider receives, so it is the operator's
     decision; do not deploy until it is empty.
3. **The access token's `aud` and `azp` equal the client id.** Check them in Authentik's provider "Preview" tab (the
   claims of a token for a chosen user).
   - With no preview: after step 5 of the deploy, make one tool call as the operator. Then run
     `docker logs openbrain-mcp 2>&1 | Select-String 'user token is not valid'`, which prints the reason, never the
     token. `wrong audience` / `wrong authorized party` = this check failed.
   - If it fails: stop, and roll back (below). Do not widen `MCP_OWUI_OIDC_AUDIENCE` to make it pass.
4. **`WORKSPACE_DEFAULT_SUB` is the operator's real sub.** Compare it, without printing either value, with the `sub`
   Open WebUI holds for the operator's own account (`oauth.oidc.sub`).
   - If they differ: stop. The operator would otherwise be served as a second user.
5. **The Proxmox edge in front of a gateway door.** openbrain-gateway forwards every client header except
   `authorization` and `x-brain-key`. A Traefik route in front of a gateway door that adds `X-authentik-*` or
   `Remote-User` (forward-auth `authResponseHeaders`) would make the full lane answer 403.
   - Check the Proxmox Traefik routers for any route to the gateway doors (`openbrain-gateway` :8061,
     `openbrain-ops-gateway` :8062; both are loopback-only on this host, so normally none).
   - If one exists: drop those headers on that route, or keep it off forward-auth, before the deploy.
6. **Back up Open WebUI's tool connections and model bindings.**
   - Admin, Settings, External Tools: export, or `GET /api/v1/configs/tool_servers` as the admin, saved to a file.
   - The model configs: `backup/models/`.
   - Note every model bound to the `open-brain` / `open-brain-extensions` OpenAPI tool servers: this is the re-binding
     list.

## Deploy (in PD1's window; never restart openwebui alone)

1. **Tag what runs now (F4), BEFORE any rebuild**, then build the new images on their own, as a deliberate deploy:
   `docker tag openbrain-mcp-server:local openbrain-mcp-server:pre-rw-mcp-identity`, and the same for
   `openbrain-ext-server` and `openbrain-workbench`.
2. Create the two keys and set the provider values (above).
3. At :x8, after the StackWatchdog's :x7 pass, recreate the three servers:
   `docker compose -f OB1/docker/docker-compose.yml up -d --no-deps --force-recreate openbrain-mcp openbrain-ext openbrain-workbench`.
   - The openbrain-ext recreate attaches it to `ai-stack_llm-net`.
   - Check both MCP servers' logs for `lanes: ... owui on (N signing key(s))`, and that every health check is healthy.
4. In Open WebUI, Admin, Settings, External Tools, add two native MCP connections (Streamable HTTP):
   - URL `http://openbrain-mcp:8000/`, Auth **OAuth** (system), custom header `x-brain-key`, ID `open-brain`, shared with
     the users who should have it;
   - the same for `http://openbrain-ext:8000/`, ID `open-brain-ext`.
5. Verify as the operator: your own rows, and `contract_ideas` shows your ideas. Verify as a second user: shared rows plus
   their own. Then disable the two mcpo OpenAPI connections, and re-bind the models from pre-check 6.
6. **F6: take the full key off the Open WebUI side.**
   - Run `python -I OB1/docker/mcpo/swap-ext-key.py --env OB1/docker/.env --config OB1/docker/mcpo-ext.config.json`. It
     reads the key NAMES, writes the personal key in place of the full one, and prints names only.
   - Then `docker compose -f OB1/docker/docker-compose.yml up -d --no-deps --force-recreate openbrain-mcpo-ext`.
   - Check: the config no longer holds `MCP_ACCESS_KEY` (compare without printing), and mcpo-ext stays healthy. It is
     inert, and is retired in a later item under the container rule.
7. Claude Code through openbrain-gateway: one `search_thoughts` and one capture, as the operator.

## Rollback (rehearsed: `run-mcp-identity-deploy-lab.ps1`)

1. `docker tag openbrain-mcp-server:pre-rw-mcp-identity openbrain-mcp-server:local`, and the same for
   `openbrain-ext-server` and `openbrain-workbench`.
2. Put the previous compose back: check OB1 out at the gitlink before this item. This is the compose in which
   openbrain-ext is on `open-brain_obnet` only.
3. `docker compose -f OB1/docker/docker-compose.yml up -d --no-deps --force-recreate openbrain-mcp openbrain-ext openbrain-workbench`.
   - openbrain-ext comes back off `ai-stack_llm-net`. If it does not, run `docker network disconnect ai-stack_llm-net openbrain-ext`.
4. `python -I OB1/docker/mcpo/swap-ext-key.py --env OB1/docker/.env --config OB1/docker/mcpo-ext.config.json --restore`.
   The pre openbrain-ext accepts only the full key. Then recreate `openbrain-mcpo-ext` alone, as in deploy step 6.
5. Open WebUI: restore the tool connections from the backup (`POST /api/v1/configs/tool_servers` with the saved file, or
   re-enable the mcpo connections and delete the two native ones), and re-bind the models from the list. No restart.
6. Check:
   - the running images are the `:pre-rw-mcp-identity` ids (`docker inspect -f '{{.Image}}'`);
   - the OWUI key is 401;
   - the personal and full lanes answer;
   - mcpo-ext lists its tools.

## Rotation

### Authentik signing key (what breaks, how to refresh)

- **What breaks:** once Authentik signs with a new key, every Open WebUI MCP request, and every workbench call
  carrying a user token, gets **401** until `MCP_OWUI_OIDC_JWKS` is refreshed.
  - This is fail-closed: no request is ever served as anyone else, or as no identity.
  - Claude Code and the agent lanes (the full key) are unaffected.
- **Refresh:**
  1. Fetch the new JWKS from the provider's `jwks_uri`.
  2. Write it to `MCP_OWUI_OIDC_JWKS` in `OB1/docker/.env`. During a planned rotation, keep the old and the new key
     together in one `keys` array.
  3. Recreate the three servers (`--no-deps --force-recreate openbrain-mcp openbrain-ext openbrain-workbench`).
  4. Drop the old key from the array at the next recreate.

### MCP_OWUI_ACCESS_KEY / OB_WORKBENCH_MCP_KEY

1. Create new values with the command above. It rewrites both lines and the `mcp=` entry.
2. Recreate the three servers.
3. Paste the new lane key into both Open WebUI connections' `x-brain-key`.

Between steps 2 and 3, OWUI requests are 401.

### MCP_ACCESS_KEY / MCP_PERSONAL_ACCESS_KEY

Unchanged by this design. After deploy step 6, the OWUI side holds the personal key only.

## Troubleshooting

- **401 on every Open WebUI tool call:** a JWKS / issuer / audience mismatch, a rotated signing key, or a JWE token
  (pre-check 2). The server log says why, without printing the token.
- **The tools answer, but only shared rows, and writes are refused:** Open WebUI sent no token. The connection is not
  `system_oauth`, or the user has no SSO session (signed in another way, or the token refresh failed). Sign out and
  back in through SSO.
- **403:** a client or proxy added a user header, or a request carries two different keys (pre-check 5).
- **The contract tools return "the research workspace is not configured for this lane":** `OB_WORKBENCH_MCP_KEY` is
  unset, or not in `WORKSPACE_SERVICE_KEYS` as `mcp=`.
- **The StackWatchdog (every 10 min, at :x7)** may restart a container in the middle of a change. Make recreates at
  :x8, then check health.
