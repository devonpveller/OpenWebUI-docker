# Open Brain per-user MCP identity: Open WebUI connections, keys and rotation

How Open WebUI's Open Brain tools act as the signed-in user (research-workbench `rw-mcp-identity`), what to configure,
how to swap Open WebUI's connections over, and how to rotate each credential. Code: `OB1/integrations/kubernetes-deployment/mcp-identity.ts`
(a byte-identical copy in `OB1/docker/extensions-server/`).

## How it works

| Lane (key in `x-brain-key`) | Who holds it | Acts as |
|---|---|---|
| `MCP_ACCESS_KEY` (full) | openbrain-gateway (Claude Code / Desktop), agent-bridge, OB1 services | **the operator** = `WORKSPACE_DEFAULT_SUB` on openbrain-mcp. On openbrain-ext its only holder is openbrain-mcpo-ext, so there it has **no identity** |
| `MCP_PERSONAL_ACCESS_KEY` (personal) | openbrain-mcpo | **no identity** (mcpo carries no user): shared rows only, every write refused |
| `MCP_OWUI_ACCESS_KEY` (owui) | Open WebUI's native MCP connections only (webui.db) | **the signed-in user**: the `sub` of their Authentik access token, sent by Open WebUI (`auth_type: system_oauth`). No token = no identity |

- A token is accepted only when its RS256 / ES256 signature verifies against `MCP_OWUI_OIDC_JWKS`, `iss` equals
  `MCP_OWUI_OIDC_ISSUER`, `aud` holds `MCP_OWUI_OIDC_AUDIENCE` (and `azp`, when present, equals it), it has not expired
  (60 s leeway), and its `sub` is a usable subject. Any failure is **401**. Never a fallback to "no identity".
- Any other way of naming a user (`x-on-behalf-of`, `X-OpenWebUI-User-*`, `x-authentik-*`, `x-ob-user`, `x-user-id`,
  `remote-user`) is **403** on every lane. So is an `Authorization` header on the full or personal lane.
- No identity: reads return shared rows only (`user_id IS NULL`), and every write is refused, shared-knowledge writes
  included.
- openbrain-ext rows: the operator keeps `DEFAULT_USER_ID`'s rows. Every other user gets their own rows: `user_id` is a
  UUIDv5 of their subject.

## Configuration (OB1/docker/.env; names only here)

`WORKSPACE_DEFAULT_SUB` (the operator's real Authentik subject, never the word `operator`), `MCP_OWUI_ACCESS_KEY`,
`MCP_OWUI_OIDC_ISSUER`, `MCP_OWUI_OIDC_AUDIENCE`, `MCP_OWUI_OIDC_JWKS`. Set all four `MCP_OWUI_*`, or none (the lane
is then off). A half-set lane, a reused key, or an OWUI lane without `WORKSPACE_DEFAULT_SUB` stops openbrain-mcp and
openbrain-ext at start.

### Create the OWUI lane key (it is written to the .env and never printed)

From the repo root, in PowerShell:

```powershell
$f = 'OB1\docker\.env'
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
$k = ($b | ForEach-Object { $_.ToString('x2') }) -join ''
$lines = @(Get-Content $f | Where-Object { $_ -notmatch '^MCP_OWUI_ACCESS_KEY=' }) + "MCP_OWUI_ACCESS_KEY=$k"
[IO.File]::WriteAllLines((Resolve-Path $f), $lines)
Remove-Variable k, b
```

To enter the key in Open WebUI, put it on the clipboard without printing it, paste it, then clear the clipboard:
`(Select-String -Path OB1\docker\.env -Pattern '^MCP_OWUI_ACCESS_KEY=(.+)$').Matches[0].Groups[1].Value | Set-Clipboard`,
then `Set-Clipboard -Value ' '`.

### Issuer, audience, JWKS (public values)

- Issuer: the `issuer` of `<OPENID_PROVIDER_URL>` (frontend/.env), copied exactly, trailing slash included.
- Audience: Open WebUI's `OAUTH_CLIENT_ID` (frontend/.env).
- JWKS: the provider's `jwks_uri` from the same discovery document. Write it on one line, single-quoted:
  `MCP_OWUI_OIDC_JWKS='{"keys":[...]}'`. Check first that Authentik's Open WebUI provider has a **Signing Key**
  (RS256/ES256 certificate). Without one, Authentik signs with HS256 and every request is refused.
- The JWKS is static on purpose: openbrain-mcp and openbrain-ext fetch nothing, so they have no egress.

## Swap Open WebUI over (exact steps; never restart openwebui alone)

1. **Back up the bindings first.** Export the model configs (Admin > Settings > Models > export, or the existing
   `backup/models/` export) and note every model whose tools include the `open-brain` / `open-brain-extensions` OpenAPI
   tool servers. That list is the re-binding list.
2. Recreate the two servers with the lane configured: `docker compose -f OB1/docker/docker-compose.yml up -d --no-deps --force-recreate openbrain-mcp openbrain-ext`.
   (openbrain-ext joins `ai-stack_llm-net` in this recreate). From that moment the mcpo connections are shared-only, so do
   steps 3-5 straight away.
3. Admin > Settings > External Tools > **+** (MCP, Streamable HTTP):
   - URL `http://openbrain-mcp:8000/`, Auth **OAuth** (system), custom header `x-brain-key` = the key, ID `open-brain`;
   - share it with the users who should have it (access grants);
   - repeat for `http://openbrain-ext:8000/`, ID `open-brain-ext`.
4. Verify as the operator: a tool call answers with the operator's own rows. Verify as a second user: their own and the
   shared rows only.
5. Disable the two OpenAPI tool servers (`openbrain-mcpo`, `openbrain-mcpo-ext`). Re-bind the models from step 1 to the
   new connections.

Nothing here restarts openwebui. If openwebui ever has to restart: openwebui, wait until it is healthy, then tailscale.

## Rotation

### Authentik signing key (what breaks, how to refresh)

- **What breaks:** once Authentik signs with a new key, every Open WebUI MCP request carries a token whose `kid` / key is not in
  `MCP_OWUI_OIDC_JWKS`, and gets **401**. This is fail-closed: no request is ever served as anyone else or as no
  identity. Claude Code and the agent lanes (the full key) are unaffected.
- **Refresh:**
  1. Fetch the new JWKS from the provider's `jwks_uri`.
  2. Write it to `MCP_OWUI_OIDC_JWKS` in `OB1/docker/.env`. During a planned rotation, keep the old and the new key
     together in one `keys` array.
  3. `docker compose -f OB1/docker/docker-compose.yml up -d --no-deps --force-recreate openbrain-mcp openbrain-ext`.
  4. Drop the old key from the array at the next recreate.

### MCP_OWUI_ACCESS_KEY

1. Create a new value with the command above.
2. Recreate openbrain-mcp and openbrain-ext as above.
3. Paste the new value into both Open WebUI connections' `x-brain-key`.

Between steps 2 and 3, OWUI requests are 401.

### MCP_ACCESS_KEY / MCP_PERSONAL_ACCESS_KEY

Unchanged by this design. Rotate them as before (the key-rotation card). The OWUI side no longer needs the full key once
the mcpo connections are gone.

## Troubleshooting

- **401 on every Open WebUI tool call:** a JWKS / issuer / audience mismatch, or a rotated signing key. The server log
  says why without printing the token.
- **The tools answer, but only shared rows, and writes are refused:** Open WebUI sent no token. The connection is not
  `system_oauth`, or the user has no SSO session (signed in another way, or the token refresh failed). Sign out and back
  in through SSO.
- **403:** a client or proxy added a user header. Only Open WebUI's token may name a user.
- **The StackWatchdog (every 10 min, at :x7)** may restart a container in the middle of a change. Make recreates at :x8,
  then check health.
