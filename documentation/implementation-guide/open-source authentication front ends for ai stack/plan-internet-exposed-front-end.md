# Internet-Exposed Front-End — Implementation Plan

**Status:** Ready for implementation
**Target stack:** [docker-compose.yml](../../../docker-compose.yml) on Windows + Docker Desktop
**Companion research doc:** [open-source0authentication-front-end-research.md](../open-source0authentication-front-end-research.md)

---

## 1. Goal

Expose **OpenWebUI** and **Open Notebook** to the public internet behind a single authentication portal, running in parallel to the existing Tailscale path. Tailnet access continues to work unchanged.

Two access paths to the same backend containers, no overlap:

| Path | Edge | Auth | DNS |
|---|---|---|---|
| **Internet** (new) | Caddy (this plan) | Authelia (this plan) | `${PUBLIC_DOMAIN}` (real domain you own) |
| **Tailnet** (existing) | [tailscale](../../../docker-compose.yml#L77-L120) container, `tailscale serve` in [entrypoint.sh](../../../entrypoint.sh) | Each service's native login | `*.tail-xxxxx.ts.net` |

The Tailscale container, its serve configuration, and its network namespace sharing with OpenWebUI are **never modified**.

---

## 2. Architecture decisions (locked)

| Decision | Choice | Reason |
|---|---|---|
| Reverse proxy | **Caddy 2.x** | Apache 2.0, automatic Let's Encrypt, clean config syntax, runs cleanly as a container alongside Tailscale |
| Auth gateway | **Authelia 4.39 (pinned tag)** | Apache 2.0, forward-auth model, TOTP + WebAuthn, Argon2id, mature |
| Session store | **Filesystem (v1)** | Single-user, single-node — Redis is unnecessary complexity for v1. Add later if multi-instance ever happens. |
| Navigation pattern | **Pattern B: launcher hub** | Simpler than iframe shell, no response-body manipulation, doesn't fight OpenWebUI/Streamlit SPA behaviors |
| Default landing | **Hub page with auto-redirect to OpenWebUI** | Hub has OpenWebUI + Open Notebook buttons; auto-redirects to OpenWebUI after 2s if user takes no action. Honors "default = OpenWebUI" without injecting chrome into service pages. |
| IP-level dynamic banning | **Deferred to v2 (CrowdSec)** | Keep v1 PR focused. v1 covers account lockout + static IP rules + Caddy rate limiting, which is ~80% of the protection. |
| OpenWebUI auth handoff | **Trusted-header SSO** (`Remote-Email`, `Remote-Name`) | OpenWebUI supports it natively. Single login at Authelia, no double prompt. |
| Open Notebook auth | **Authelia-only** (no native auth in Open Notebook) | Mandatory edge gate — Open Notebook is unauthenticated by design. |
| Watchtower | **Disabled for new containers** | Match the existing pattern at [docker-compose.yml:114, 238, 273, 308](../../../docker-compose.yml#L114). Pin tags, update manually. |

---

## 3. Scope

### v1 — this plan, ship as one PR
- Pre-flight fixes (section 5)
- Caddy + Authelia containers added to [docker-compose.yml](../../../docker-compose.yml)
- Caddyfile with forward-auth, response-header strip, route map
- Authelia config (single user, TOTP, Argon2id, tight regulation, `access_control.networks` rules)
- Launcher hub static page
- OpenWebUI trusted-header env vars
- `.env.example` patch

### v2 — separate follow-up PR (section 8)
- CrowdSec container + caddy-bouncer
- Log shipping from Authelia + Caddy to CrowdSec
- Validation that banned IPs are rejected at Caddy

### Out of scope (do NOT include in either PR)
- Replacing Tailscale
- Exposing llama-cpp, llama-cpp-embed, mnemory, smolcrawl, open-terminal, surrealdb (these stay internal)
- Multi-user provisioning UI (Authelia file-based users DB is fine for v1)
- Email/SMTP notifier (use the `filesystem` notifier in v1; document upgrade path)
- OIDC federation to external IdPs

---

## 4. Final architecture

```
Internet
  │
  ▼  TCP 80, 443  (only inbound ports on the host)
┌─────────────────────────────────────────────┐
│ caddy (new)                                 │
│  - TLS via Let's Encrypt (auto)             │
│  - rate_limit on /api/firstfactor           │
│  - forward_auth → authelia                  │
│  - strips X-Frame-Options, sets CSP/HSTS    │
└────┬──────────────────┬─────────────────────┘
     │ forward_auth     │ proxied routes
     ▼                  │
┌──────────────┐        │
│ authelia     │        │
│  (new)       │        │
│  - TOTP/MFA  │        │
│  - regulation│        │
│  - access_   │        │
│    control   │        │
└──────────────┘        │
                        ▼
       ┌────────────────┼────────────────┬──────────────┐
       ▼                ▼                ▼              ▼
   /  (hub)      /openwebui/*    /notebook/*     /api/notebook/*
   static HTML   openwebui:8080  open_notebook   open_notebook
   from caddy    (existing)      :8502           :5055
                                 (existing)      (existing)

Tailscale path (unchanged):
  tailnet  →  tailscale container (shared netns with openwebui)
            →  tailscale serve  →  127.0.0.1 ports inside openwebui ns
```

**Network:** `caddy` and `authelia` sit on the existing `default` Docker bridge. They reach `openwebui` and `open_notebook` by container DNS. `llm-net` stays untouched and `internal: true`.

---

## 5. Pre-flight fixes (REQUIRED — block v1 until done)

These are pre-existing exposure issues. They must land before Caddy is opened to the internet, otherwise services leak around the auth portal.

### 5.1 Bind `open_notebook` to localhost
[docker-compose.yml:428-430](../../../docker-compose.yml#L428-L430)
```yaml
    ports:
      - "127.0.0.1:8503:8502"  # was: "8503:8502"
      - "127.0.0.1:5055:5055"  # was: "5055:5055"
```

### 5.2 Bind `surrealdb` to localhost
[docker-compose.yml:411-413](../../../docker-compose.yml#L411-L413)
```yaml
    ports:
      - "127.0.0.1:8003:8000"  # was: "8003:8000"
```

### 5.3 Move SurrealDB credentials to `.env`
[docker-compose.yml:411, 439-440](../../../docker-compose.yml#L411)
- Add to `.env`: `SURREAL_USER=<new>`, `SURREAL_PASSWORD=<strong random>`
- Replace hardcoded `root` / `root` in compose `command:` and `SURREAL_PASSWORD` env with `${SURREAL_USER}` / `${SURREAL_PASSWORD}`
- After change: stop stack, wipe `D:\Open WebUI\open-notebook\surreal_data` only if you accept data loss, OR migrate creds with `surreal sql` — confirm with user before destroying data

### 5.4 Move Open Notebook encryption key to `.env`
[docker-compose.yml:434](../../../docker-compose.yml#L434)
- Add to `.env`: `OPEN_NOTEBOOK_ENCRYPTION_KEY=<random 32+ char string>`
- Replace hardcoded value with `${OPEN_NOTEBOOK_ENCRYPTION_KEY}`
- **Warning:** changing this key invalidates already-stored encrypted API keys in SurrealDB. Confirm with user. If keeping existing data, keep the existing key value and just move it to `.env`.

---

## 6. v1 file changes

### 6.1 New files
```
config/
  caddy/
    Caddyfile
    site/
      index.html              # the launcher hub
      hub.css                 # minimal styling
  authelia/
    configuration.yml
    users_database.yml        # gitignored after first commit of template
    .gitignore
```

### 6.2 Modified files
```
docker-compose.yml            # add caddy, authelia services + named volumes
.env.example                  # add new secrets template
.gitignore                    # ensure config/authelia/users_database.yml is ignored
```

### 6.3 Volumes added to [docker-compose.yml](../../../docker-compose.yml)
```yaml
volumes:
  caddy-data:        # ACME certs, OCSP staples
  caddy-config:      # Caddy runtime config
  authelia-data:     # Authelia notifications + sqlite storage
```

---

## 7. v1 implementation steps

### Step 1 — Add new services to [docker-compose.yml](../../../docker-compose.yml)

Append to the `services:` block (do **not** modify existing services beyond Step 5 above and Step 5 below):

```yaml
  caddy:
    image: caddy:2.8
    container_name: caddy
    networks:
      - default
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"  # HTTP/3
    volumes:
      - ./config/caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - ./config/caddy/site:/srv/site:ro
      - caddy-data:/data
      - caddy-config:/config
    environment:
      - PUBLIC_DOMAIN=${PUBLIC_DOMAIN}
      - ACME_EMAIL=${ACME_EMAIL}
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    depends_on:
      authelia:
        condition: service_started
      openwebui:
        condition: service_healthy
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:80/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s

  authelia:
    image: authelia/authelia:4.39
    container_name: authelia
    networks:
      - default
    volumes:
      - ./config/authelia:/config:ro
      - authelia-data:/data
    environment:
      - TZ=UTC
      - AUTHELIA_JWT_SECRET=${AUTHELIA_JWT_SECRET}
      - AUTHELIA_SESSION_SECRET=${AUTHELIA_SESSION_SECRET}
      - AUTHELIA_STORAGE_ENCRYPTION_KEY=${AUTHELIA_STORAGE_ENCRYPTION_KEY}
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:9091/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
```

Then append to the `volumes:` block:
```yaml
  caddy-data:
  caddy-config:
  authelia-data:
```

### Step 2 — Create `config/caddy/Caddyfile`

```caddy
{
    email {$ACME_EMAIL}
    # Use the staging endpoint while testing; remove for prod
    # acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
}

# Authelia portal — the auth UI itself lives at auth.<domain>
auth.{$PUBLIC_DOMAIN} {
    reverse_proxy authelia:9091
    encode zstd gzip

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options    "nosniff"
        Referrer-Policy           "strict-origin-when-cross-origin"
        -Server
    }
}

# Main app domain — hub + reverse-proxied services
{$PUBLIC_DOMAIN} {
    encode zstd gzip

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options    "nosniff"
        Referrer-Policy           "strict-origin-when-cross-origin"
        -Server
    }

    # Health endpoint for the compose healthcheck (no auth)
    handle /healthz {
        respond "ok" 200
    }

    # Forward-auth check: every request below goes through Authelia first
    forward_auth authelia:9091 {
        uri /api/verify?rd=https://auth.{$PUBLIC_DOMAIN}/
        copy_headers Remote-User Remote-Groups Remote-Name Remote-Email
    }

    # Rate-limit login submissions to slow brute force at the edge.
    # Requires the caddy-ratelimit module (build with xcaddy or use a tag
    # that includes it). If not using a custom build, remove this block
    # and rely on Authelia regulation + (later) CrowdSec.
    # @auth_post {
    #     method POST
    #     path /api/firstfactor /api/secondfactor/*
    # }
    # rate_limit @auth_post {
    #     zone auth_login
    #     key  {remote_host}
    #     events 5
    #     window 1m
    # }

    # --- Routes ---

    # OpenWebUI under /openwebui/
    handle_path /openwebui/* {
        reverse_proxy openwebui:8080 {
            header_up Host {upstream_hostport}
            header_up X-Forwarded-Proto https
            # Drop iframe lockdown so future UI shells stay possible
            header_down -X-Frame-Options
            header_down -Content-Security-Policy
        }
    }

    # Open Notebook UI under /notebook/
    # NOTE: Streamlit subpath support requires --server.baseUrlPath=/notebook
    #       on the open_notebook container. If subpath proves brittle, switch
    #       to a subdomain (notebook.<domain>) — see "Subpath caveat" below.
    handle_path /notebook/* {
        reverse_proxy open_notebook:8502 {
            header_up Host {upstream_hostport}
            header_up X-Forwarded-Proto https
            header_down -X-Frame-Options
        }
    }

    # Open Notebook API under /api/notebook/
    handle_path /api/notebook/* {
        reverse_proxy open_notebook:5055 {
            header_up Host {upstream_hostport}
            header_up X-Forwarded-Proto https
        }
    }

    # Hub — static landing page with the service buttons
    handle {
        root * /srv/site
        try_files {path} /index.html
        file_server
    }
}
```

**Subpath caveat to surface to user:**
Streamlit (Open Notebook UI) and OpenWebUI both have intermittent issues with subpath deployment. If the implementing agent hits 404s on assets or broken WebSockets:
- **Fallback A:** Switch to subdomains: `app.{$PUBLIC_DOMAIN}` → OpenWebUI, `notebook.{$PUBLIC_DOMAIN}` → Open Notebook. Hub stays at `{$PUBLIC_DOMAIN}`. Requires wildcard cert or per-subdomain certs (Caddy handles either).
- **Fallback B:** For Open Notebook specifically, configure `--server.baseUrlPath=/notebook` via container env vars and verify Streamlit assets load.
Document whichever fallback was needed in the PR.

### Step 3 — Create `config/authelia/configuration.yml`

```yaml
---
theme: dark
default_2fa_method: totp
server:
  address: tcp://0.0.0.0:9091
  endpoints:
    authz:
      auth-request:
        implementation: AuthRequest

log:
  level: info
  format: text

totp:
  issuer: AI Stack
  algorithm: sha1
  digits: 6
  period: 30

webauthn:
  display_name: AI Stack
  attestation_conveyance_preference: none

authentication_backend:
  password_reset:
    disable: true
  file:
    path: /config/users_database.yml
    password:
      algorithm: argon2
      argon2:
        variant: argon2id
        iterations: 3
        memory: 65536
        parallelism: 4
        key_length: 32
        salt_length: 16

# IP-aware policy. Tighten the 'networks' list for your LAN.
access_control:
  default_policy: deny
  networks:
    - name: home_lan
      networks:
        - 192.168.0.0/16
        - 10.0.0.0/8
  rules:
    # Internal LAN: single-factor is enough
    - domain: '{{ env "PUBLIC_DOMAIN" }}'
      policy: one_factor
      networks: ['home_lan']
    # Anyone else on the internet: must complete 2FA
    - domain: '{{ env "PUBLIC_DOMAIN" }}'
      policy: two_factor
    - domain: 'auth.{{ env "PUBLIC_DOMAIN" }}'
      policy: bypass

session:
  name: authelia_session
  same_site: lax
  inactivity: 30m
  expiration: 12h
  remember_me: 1M
  cookies:
    - domain: '{{ env "PUBLIC_DOMAIN" }}'
      authelia_url: 'https://auth.{{ env "PUBLIC_DOMAIN" }}'

# Account-level lockout (per-username, NOT per-IP — that's CrowdSec's job in v2)
regulation:
  max_retries: 3
  find_time: 2m
  ban_time: 1h

storage:
  local:
    path: /data/db.sqlite3

notifier:
  filesystem:
    filename: /data/notification.txt
  # For v2 / production, swap for an SMTP block:
  # smtp:
  #   address: 'submission://smtp.example.com:587'
  #   username: ...
  #   password: ...
  #   sender: ...
```

**Note:** Authelia 4.39 supports `{{ env "VAR" }}` Go-template substitution in config. The `PUBLIC_DOMAIN` env var must be passed into the Authelia container — add it to the `environment:` block in [docker-compose.yml](../../../docker-compose.yml).

Update Step 1's `authelia` service block to add:
```yaml
      - PUBLIC_DOMAIN=${PUBLIC_DOMAIN}
```

### Step 4 — Create `config/authelia/users_database.yml`

Template only — actual file must be **gitignored** and generated locally:

```yaml
---
users:
  yourusername:
    disabled: false
    displayname: "Your Name"
    # Generate with: docker run --rm authelia/authelia:4.39 authelia crypto hash generate argon2
    password: "$argon2id$v=19$m=65536,t=3,p=4$..."
    email: yourusername@example.com
    groups:
      - admins
```

Add to `config/authelia/.gitignore`:
```
users_database.yml
```

Add to the top-level `.gitignore`:
```
/config/authelia/users_database.yml
```

### Step 5 — Create the launcher hub

`config/caddy/site/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Stack</title>
  <link rel="stylesheet" href="/hub.css">
  <meta http-equiv="refresh" content="3; url=/openwebui/">
</head>
<body>
  <header class="hub-header">
    <h1>AI Stack</h1>
    <p class="hint">Redirecting to Open WebUI in 3 seconds &mdash; or pick a service below.</p>
  </header>
  <main class="hub-grid">
    <a class="hub-card primary" href="/openwebui/">
      <h2>Open WebUI</h2>
      <p>Chat, models, and tools</p>
    </a>
    <a class="hub-card" href="/notebook/">
      <h2>Open Notebook</h2>
      <p>Documents and notebooks</p>
    </a>
  </main>
  <footer class="hub-footer">
    <a href="https://auth.${PUBLIC_DOMAIN}/logout">Sign out</a>
  </footer>
  <script>
    // Cancel the auto-redirect if user hovers any card
    document.querySelectorAll('.hub-card').forEach(c => {
      c.addEventListener('mouseenter', () => {
        const meta = document.querySelector('meta[http-equiv="refresh"]');
        if (meta) meta.remove();
      });
    });
  </script>
</body>
</html>
```

`config/caddy/site/hub.css`:

```css
:root {
  --bg: #0f1116;
  --fg: #e7e9ee;
  --muted: #8a92a3;
  --card: #1a1d26;
  --card-hover: #232735;
  --accent: #4f8cff;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  font-family: system-ui, -apple-system, Segoe UI, sans-serif;
  background: var(--bg);
  color: var(--fg);
  display: flex;
  flex-direction: column;
}
.hub-header {
  padding: 4rem 2rem 1rem;
  text-align: center;
}
.hub-header h1 {
  margin: 0;
  font-size: 2.5rem;
  letter-spacing: -0.02em;
}
.hub-header .hint {
  color: var(--muted);
  margin: 0.5rem 0 0;
}
.hub-grid {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1rem;
  padding: 2rem;
  max-width: 900px;
  width: 100%;
  margin: 0 auto;
  align-content: center;
}
.hub-card {
  display: block;
  padding: 2rem;
  background: var(--card);
  border: 1px solid transparent;
  border-radius: 12px;
  text-decoration: none;
  color: var(--fg);
  transition: background 0.15s, border-color 0.15s, transform 0.15s;
}
.hub-card:hover {
  background: var(--card-hover);
  border-color: var(--accent);
  transform: translateY(-2px);
}
.hub-card.primary {
  border-color: var(--accent);
}
.hub-card h2 {
  margin: 0 0 0.5rem;
  font-size: 1.25rem;
}
.hub-card p {
  margin: 0;
  color: var(--muted);
}
.hub-footer {
  padding: 1rem;
  text-align: center;
  color: var(--muted);
  font-size: 0.875rem;
}
.hub-footer a {
  color: var(--muted);
}
```

**Note on the `${PUBLIC_DOMAIN}` token in `index.html`:** Caddy's `file_server` does not substitute env vars in static files. Two options:
- **A (recommended):** Hardcode the logout URL or use a relative path `/logout` and have Authelia handle the route. Authelia's logout endpoint is at `https://auth.${PUBLIC_DOMAIN}/logout` — use that absolute URL with the domain baked in at deploy time via a `sed` in a build step, or
- **B:** Wrap the static serve in Caddy's `templates` directive, which supports `{{ env "PUBLIC_DOMAIN" }}` substitution. Add `templates` to the `handle` block that serves the hub.

Use option B for cleanliness:
```caddy
handle {
    root * /srv/site
    templates
    try_files {path} /index.html
    file_server
}
```
And change the HTML to `{{ env "PUBLIC_DOMAIN" }}` instead of `${PUBLIC_DOMAIN}`.

### Step 6 — OpenWebUI trusted-header SSO

In [docker-compose.yml:18-37](../../../docker-compose.yml#L18-L37) `openwebui.environment`, **add** (do not remove anything):

```yaml
      - WEBUI_AUTH_TRUSTED_EMAIL_HEADER=Remote-Email
      - WEBUI_AUTH_TRUSTED_NAME_HEADER=Remote-Name
```

This makes OpenWebUI accept Authelia-issued headers. Tailnet access still works because when those headers are absent (Tailscale serve does not inject them), OpenWebUI falls back to its native login.

### Step 7 — `.env.example` patch

Add to `.env.example`:
```bash
# === Internet-exposed front-end ===
PUBLIC_DOMAIN=ai.example.com
ACME_EMAIL=you@example.com

# Authelia secrets — generate with:
#   docker run --rm authelia/authelia:4.39 authelia crypto rand --length 64
AUTHELIA_JWT_SECRET=
AUTHELIA_SESSION_SECRET=
AUTHELIA_STORAGE_ENCRYPTION_KEY=

# SurrealDB credentials (pre-flight 5.3)
SURREAL_USER=
SURREAL_PASSWORD=

# Open Notebook DB encryption key (pre-flight 5.4)
OPEN_NOTEBOOK_ENCRYPTION_KEY=
```

---

## 8. v2 — CrowdSec follow-up (separate PR)

### 8.1 What CrowdSec adds

| Function | v1 (Authelia alone) | v2 (with CrowdSec) |
|---|---|---|
| Account lockout after N failures | Yes (per-username) | Yes (per-username, unchanged) |
| Static IP allow/deny | Yes (`access_control.networks`) | Yes (unchanged) |
| Caddy edge rate-limit on `/api/firstfactor` | Optional (custom Caddy build) | Yes (CrowdSec scenario fires faster) |
| **Dynamic per-IP ban after N failures** | **No** | **Yes** — CrowdSec parses Authelia + Caddy logs, decisions enforced by the caddy-bouncer |
| Community threat-intel feed (pre-block known-bad IPs) | No | Yes (opt-in, free) |
| Centralized decisions visible via `cscli` | No | Yes |

### 8.2 Files added in v2

```
config/
  crowdsec/
    acquis.yaml              # log sources
    profiles.yaml            # decision profiles
  caddy/
    Caddyfile                # ← MODIFIED to load the bouncer plugin
```

### 8.3 Containers added in v2

```yaml
  crowdsec:
    image: crowdsecurity/crowdsec:latest
    container_name: crowdsec
    networks:
      - default
    volumes:
      - ./config/crowdsec/acquis.yaml:/etc/crowdsec/acquis.yaml:ro
      - ./config/crowdsec/profiles.yaml:/etc/crowdsec/profiles.yaml:ro
      - crowdsec-data:/var/lib/crowdsec/data
      - crowdsec-config:/etc/crowdsec
      # Read-only log mounts from the services we want to protect:
      - authelia-data:/logs/authelia:ro
      - caddy-data:/logs/caddy:ro
    environment:
      - TZ=UTC
      - COLLECTIONS=crowdsecurity/caddy crowdsecurity/appsec-virtual-patching
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
```

Pin to a specific minor tag in the actual PR (`crowdsecurity/crowdsec:v1.6.4` or current at implementation time) — `latest` listed here only for plan readability.

### 8.4 Caddy bouncer

CrowdSec enforces at Caddy via a plugin. Two options:
- **Custom Caddy build with `caddy-crowdsec-bouncer`** — replace `caddy:2.8` image with a custom build using `xcaddy`. Add a `Dockerfile.caddy` to the repo:
  ```dockerfile
  FROM caddy:2.8-builder AS builder
  RUN xcaddy build \
        --with github.com/hslatman/caddy-crowdsec-bouncer/http \
        --with github.com/mholt/caddy-ratelimit

  FROM caddy:2.8
  COPY --from=builder /usr/bin/caddy /usr/bin/caddy
  ```
  And update the `caddy` service in [docker-compose.yml](../../../docker-compose.yml) to `build:` instead of `image:`.

- **Sidecar HTTP bouncer** — alternative without rebuilding Caddy: run `crowdsecurity/cs-firewall-bouncer` or a generic HTTP bouncer that returns 403 via a Caddy `forward_auth`-like check. Less elegant; only use if custom Caddy build is undesirable.

**Default choice for v2:** custom Caddy build. The `xcaddy` build also picks up `caddy-ratelimit`, so the rate-limit block commented out in Step 2's Caddyfile can be uncommented at the same time.

### 8.5 Caddyfile changes in v2

Add to the global block:
```caddy
{
    email {$ACME_EMAIL}
    order crowdsec first
    crowdsec {
        api_url http://crowdsec:8080
        api_key {$CROWDSEC_BOUNCER_KEY}
        ticker_interval 10s
    }
}
```

Add `crowdsec` directive inside each site block before `forward_auth`:
```caddy
{$PUBLIC_DOMAIN} {
    crowdsec
    forward_auth authelia:9091 {
        ...
    }
    ...
}
```

### 8.6 `acquis.yaml` (CrowdSec log sources)

```yaml
---
filenames:
  - /logs/authelia/notification.txt   # not actually authelia auth logs — see note
labels:
  type: authelia
---
filenames:
  - /logs/caddy/access.log
labels:
  type: caddy
```

**Note for the implementing agent:** Authelia's auth event logs are emitted to stdout by default (per the `log:` block in `configuration.yml`). To make them ingestable by CrowdSec:
- Switch Authelia to write structured logs to a file: set `log.file_path: /data/authelia.log` in `configuration.yml`.
- Mount the same `authelia-data` volume into CrowdSec read-only (already wired in 8.3).
- Update `acquis.yaml` to point at `/logs/authelia/authelia.log`.

Caddy access logs require enabling in the Caddyfile:
```caddy
{$PUBLIC_DOMAIN} {
    log {
        output file /data/access.log
        format json
    }
    ...
}
```
And ensuring `/data` inside the Caddy container is the `caddy-data` volume (it already is).

### 8.7 Bouncer registration

After `crowdsec` is running, register the Caddy bouncer to get the API key:
```powershell
docker exec crowdsec cscli bouncers add caddy
```
Capture the key, add it to `.env` as `CROWDSEC_BOUNCER_KEY=...`, restart Caddy.

### 8.8 Suggested CrowdSec scenarios to enable

```powershell
docker exec crowdsec cscli collections install crowdsecurity/authelia
docker exec crowdsec cscli collections install crowdsecurity/caddy
docker exec crowdsec cscli collections install crowdsecurity/http-cve
docker exec crowdsec cscli collections install crowdsecurity/base-http-scenarios
```

### 8.9 v2 validation

- Trigger a synthetic brute-force from a non-LAN IP (use a phone hotspot or external VM):
  ```bash
  for i in $(seq 1 10); do curl -s -X POST https://auth.${PUBLIC_DOMAIN}/api/firstfactor \
    -H 'Content-Type: application/json' \
    -d '{"username":"nope","password":"nope","targetURL":"https://${PUBLIC_DOMAIN}/"}'; done
  ```
- Verify ban: `docker exec crowdsec cscli decisions list` should show the source IP.
- Verify enforcement: same IP making a fresh request to `https://${PUBLIC_DOMAIN}/` should receive Caddy 403, NOT reach Authelia.
- Verify whitelist: LAN IPs in `access_control.networks.home_lan` should still pass even if they trip a scenario.

---

## 9. v1 validation checklist

After implementing sections 5–7 and before merging:

### Pre-deploy
- [ ] `.env` has all new vars populated (random 64-char strings for Authelia secrets)
- [ ] `users_database.yml` has at least one user with an Argon2id-hashed password
- [ ] `PUBLIC_DOMAIN` resolves to the host's WAN IP
- [ ] Router forwards TCP 80 + 443 + UDP 443 to the host
- [ ] Windows Defender Firewall allows inbound 80/443/UDP 443 for Docker Desktop

### Deploy
- [ ] `docker compose pull && docker compose up -d caddy authelia` (note: don't restart unrelated services)
- [ ] `docker logs caddy` shows no errors, ACME cert issued
- [ ] `docker logs authelia` shows "Authelia is listening on tcp://0.0.0.0:9091"

### Functional
- [ ] `https://auth.${PUBLIC_DOMAIN}/` loads Authelia login screen
- [ ] `https://${PUBLIC_DOMAIN}/` redirects to Authelia → after login, hub appears
- [ ] Hub auto-redirects to OpenWebUI after 3s
- [ ] Hovering a hub card cancels the auto-redirect
- [ ] OpenWebUI loads at `https://${PUBLIC_DOMAIN}/openwebui/` and shows the Authelia-authenticated user (no second login prompt)
- [ ] Open Notebook loads at `https://${PUBLIC_DOMAIN}/notebook/` — Streamlit assets load, sidebar works
- [ ] Open Notebook API calls succeed from browser devtools network tab
- [ ] **Tailscale path unaffected:** `https://<tailnet-host>.ts.net/` still loads OpenWebUI's native login
- [ ] Sign out from Authelia → next request to `${PUBLIC_DOMAIN}/openwebui/` requires re-auth

### Security smoke tests
- [ ] 4 failed logins in 2 minutes locks the account for 1 hour (verify in Authelia logs)
- [ ] HTTPS only — `http://${PUBLIC_DOMAIN}/` redirects to HTTPS
- [ ] `curl -I https://${PUBLIC_DOMAIN}/` shows `Strict-Transport-Security` header
- [ ] LAN IPs hit `one_factor`, external IPs hit `two_factor` (test from phone on cell data)
- [ ] llama-cpp / llama-cpp-embed / mnemory / surrealdb / open-terminal are NOT reachable from internet — `curl https://${PUBLIC_DOMAIN}/api/embeddings` (or similar) returns 404 from Caddy

---

## 10. Operational notes

### 10.1 Update policy
- **Caddy:** safe to bump minor versions. Tag pinned to `2.8` in [docker-compose.yml](../../../docker-compose.yml). Bump explicitly in a PR.
- **Authelia:** Authelia has historically renamed config fields between minor versions. Read the changelog before bumping past `4.39`. Stay on a pinned minor.
- **CrowdSec (v2):** safe to bump minors; scenarios update independently via `cscli`.

### 10.2 Backup additions
The existing [`openwebui-backup`](../../../docker-compose.yml#L310-L337) and [`mnemory-backup`](../../../docker-compose.yml#L281-L308) services back up named volumes via cron. Add equivalent backups for:
- `authelia-data` — contains the user notification file (for password resets if SMTP added later) and the SQLite TOTP secrets store. Losing this means users must re-enroll TOTP.
- `caddy-data` — contains ACME certs. Losing this means Let's Encrypt re-issues, which is fine but eats your renewal quota if it happens often.

Defer the backup additions to v3 unless trivial.

### 10.3 Cert renewal monitoring
Caddy auto-renews. Add a healthcheck or log alert for ACME failures. Caddy logs the renewal events at `info` level; pipe them into the existing log aggregation (see [logs/](../../../logs/) dir conventions).

### 10.4 What to tell users
- One bookmark: `https://${PUBLIC_DOMAIN}/`
- First-time setup: TOTP enrollment QR appears at first login; scan with Authy / Google Authenticator / 1Password
- Password reset (v1): no SMTP yet — admin manually edits `users_database.yml` and re-hashes via `docker run --rm authelia/authelia:4.39 authelia crypto hash generate argon2`
- Sign out: `https://auth.${PUBLIC_DOMAIN}/logout`

### 10.5 Things that will surprise you
- Caddy's `forward_auth` requires the Authelia endpoint to be `/api/verify?rd=...`, not `/api/authz/...` — the latter is for newer authz handlers and behaves differently
- If subpath proves brittle for Streamlit (Step 2 caveat), the subdomain fallback is the right answer — don't fight the framework
- Open Notebook's frontend auto-detects its API URL from `X-Forwarded-Proto`/`Host` ([entrypoint.sh:384-389](../../../entrypoint.sh#L384-L389)). Caddy `header_up X-Forwarded-Proto https` is non-optional
- Authelia's `default_policy: deny` means **everything is denied unless explicitly allowed by a rule** — easy to lock yourself out during config changes. Always test changes by leaving an existing session open in another browser tab

---

## 11. References

- Authelia docs: https://www.authelia.com/configuration/
- Authelia forward-auth with Caddy: https://www.authelia.com/integration/proxies/caddy/
- Caddy forward-auth: https://caddyserver.com/docs/caddyfile/directives/forward_auth
- OpenWebUI trusted-header auth: search OpenWebUI docs for `WEBUI_AUTH_TRUSTED_EMAIL_HEADER`
- CrowdSec + Caddy bouncer: https://github.com/hslatman/caddy-crowdsec-bouncer
- CrowdSec collections hub: https://hub.crowdsec.net/

---

## 12. Handoff notes for the implementing agent

**Order of operations:**
1. Read [docker-compose.yml](../../../docker-compose.yml) and [entrypoint.sh](../../../entrypoint.sh) end-to-end before changing anything. Confirm your mental model matches section 4.
2. Land **section 5 pre-flight fixes** in the same PR as the v1 work. The exposure they close is unsafe to leave once Caddy opens ports 80/443.
3. Generate secrets (`AUTHELIA_*`, `SURREAL_PASSWORD`, `OPEN_NOTEBOOK_ENCRYPTION_KEY`) and have the user paste them into their `.env`. Do not commit `.env`.
4. Start `authelia` first, confirm health, then `caddy`. Use Let's Encrypt **staging** endpoint first (commented line in the Caddyfile) until the full flow works, then switch to prod to avoid rate-limiting your account.
5. Walk the section 9 validation checklist. Do not declare done until every box is ticked. The Tailscale-path-still-works check is the most important one.
6. v2 (CrowdSec) is a **separate PR**. Do not bundle.

**When to stop and ask the user:**
- Before destroying any data in `D:\Open WebUI\open-notebook\surreal_data` (pre-flight 5.3)
- Before changing `OPEN_NOTEBOOK_ENCRYPTION_KEY` if the user has any stored API keys in Open Notebook (pre-flight 5.4)
- If subpath routing for Open Notebook / OpenWebUI doesn't work and the fallback is subdomain — confirm the user owns the subdomains and wants to add DNS records

**Definition of done for v1:**
A fresh device, NOT on the user's tailnet, can browse to `https://${PUBLIC_DOMAIN}/`, authenticate via Authelia (TOTP), land on the hub, auto-redirect to OpenWebUI, use OpenWebUI normally, click back to the hub, switch to Open Notebook, use it normally. Meanwhile a separate device on the tailnet can still reach `https://<tailnet-host>.ts.net/` without seeing Authelia at all.
