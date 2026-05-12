## Open-Source Authentication Front-Ends for Your AI Stack

### The Architecture You're Describing

Your goal: expose Open WebUI, Open Notebook, and other AI-stack services outside Tailscale while adding a **single authentication front-end**. The standard pattern is:

```
Internet → [Reverse Proxy] → [Auth Gateway] → [Your AI Services]
                     ↕
               [WireGuard / VPN Layer]
```

---

## 🔑 Option 1 — Authelia (Lightweight Forward-Auth Gateway)

**Best for:** Simplicity. You want one login screen in front of everything, minimal config.

| Feature             | Detail                                                                                                                                                                       |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Type**            | Forward-auth gateway + OIDC provider                                                                                                                                         |
| **License**         | Apache 2.0 (fully open source)                                                                                                                                               |
| **What it does**    | Sits behind your reverse proxy. Every request to any backend service is intercepted — user must authenticate. Supports 2FA, session management, and SSO across all services. |
| **Integrates with** | Nginx, Nginx Proxy Manager, Traefik, Caddy, Apache (official docs for all)                                                                                                   |
| **Deployment**      | Docker / Docker Compose — very lightweight (~50MB)                                                                                                                           |
| **Auth methods**    | Password, TOTP, WebAuthn, Duo, OIDC, LDAP                                                                                                                                    |
| **Complexity**      | Low. Single YAML config file + reverse proxy integration                                                                                                                     |

**Why it fits your use case:** You already have Open WebUI with its own login. Authelia sits _before_ that, so even if a service has no auth (like Open Notebook), it gets protected. One login for the whole stack.

---

## 🔑 Option 2 — Authentik (Full Identity Provider)

**Best for:** Flexibility. You want granular control, multiple auth sources, or plan to scale.

| Feature             | Detail                                                                                                                           |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Type**            | Full IdP (Identity Provider) — more powerful than Authelia                                                                       |
| **License**         | BSD (open source core); Enterprise tier is paid                                                                                  |
| **What it does**    | Complete identity platform: user directories, policy engine, protocol bridging (SAML ↔ OAuth2 ↔ OIDC), flow-based authentication |
| **Integrates with** | Nginx, Traefik, Caddy, Cloudflare Tunnel, any OIDC/SAML/OAuth2-capable service                                                   |
| **Deployment**      | Docker Compose (requires PostgreSQL backend)                                                                                     |
| **Auth methods**    | Everything — passwords, MFA, social login, LDAP/AD, SCIM, device trust                                                           |
| **Complexity**      | Medium-High. More moving parts, but far more configurable                                                                        |

**Why it fits your use case:** If you want per-service policies (e.g., "Open Notebook requires MFA, Open WebUI uses its own auth but Authentik gates it"), or if you plan to add more services over time, Authentik gives you the most control.

---

## 🔑 Option 3 — Pangolin (WireGuard-Native, Identity-Aware VPN + Reverse Proxy)

**Best for:** When you specifically want WireGuard as the transport layer with built-in authentication.

| Feature             | Detail                                                                                                                                                                                          |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Type**            | Identity-aware VPN + tunneled reverse proxy (built _on_ WireGuard)                                                                                                                              |
| **License**         | Open Source (Community Edition free; Cloud tier paid)                                                                                                                                           |
| **What it does**    | Replaces both WireGuard _and_ your reverse proxy. Clients connect via WireGuard tunnels, and the integrated reverse proxy gives clientless web access to internal apps with identity-based auth |
| **Integrates with** | Self-contained — no external reverse proxy needed                                                                                                                                               |
| **Deployment**      | Docker / Docker Compose                                                                                                                                                                         |
| **Clients**         | Windows GUI, Linux CLI                                                                                                                                                                          |
| **Complexity**      | Medium. It consolidates multiple roles but is less widely adopted                                                                                                                               |

**Why this matches your WireGuard mention:** This is the only option that _actually_ uses WireGuard natively. Everything else uses WireGuard as a separate VPN layer in front of the proxy.

---

## 🔑 Option 4 — Caddy + Internal Auth (Minimal Stack)

**Best for:** "I just want something working tomorrow with minimal moving parts."

| Feature          | Detail                                                                                                             |
| ---------------- | ------------------------------------------------------------------------------------------------------------------ |
| **Type**         | Reverse proxy with built-in auth middleware                                                                        |
| **License**      | Apache 2.0 + GPL (fully open source)                                                                               |
| **What it does** | Automatic HTTPS (Let's Encrypt), reverse proxy, and basic auth/OIDC auth built in — no separate auth server needed |
| **Complexity**   | Very Low. One Caddyfile configures everything                                                                      |

**Why it fits:** If you don't need SSO across services and just need each service behind a login, Caddy can handle that natively without Authelia or Authentik.

---

## 🛡️ Key Security Considerations

### Network Layer

- **Never port-forward directly to services.** Always use a reverse proxy as the single entry point.
- **Use TLS everywhere.** Let's Encrypt (automatic with Caddy, Certbot with Nginx) is free and essential.
- **Keep unexposed services off the internet entirely.** Only expose what you need through the proxy.

### Authentication Layer

- **Enable 2FA/MFA.** Single-password auth is not enough for internet-facing services, especially for AI tools that may execute code.
- **Use session timeouts.** Limit idle session duration to reduce risk from hijacked sessions.
- **Implement rate limiting.** Protect against brute-force attacks on login endpoints.

### Application Layer

- **Security headers matter.** Enforce HSTS, X-Frame-Options, X-Content-Type-Options, CSP where possible.
- **Isolate your AI stack with Docker networks.** Even if the proxy is compromised, backend services shouldn't be reachable from each other unless needed.
- **Never expose management dashboards** (Traefik dashboard on :8080, Nginx Proxy Manager, etc.) without auth and ideally not to the public internet at all.

### WireGuard-Specific (if you use it as a VPN layer)

- **Key rotation.** WireGuard keys should be rotated periodically.
- **Restrict allowed IPs.** Use the `AllowedIPs` directive to limit what a client can reach through the tunnel.
- **Persistent keepalive for NAT.** If connecting from behind NAT routers, configure `PersistentKeepalive`.

---

## ✅ DOs and ❌ DON'Ts

| DO                                                                             | DON'T                                                                                        |
| ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| **DO** use a reverse proxy as the single ingress point                         | **DON'T** expose services directly via port forwarding                                       |
| **DO** enforce HTTPS/TLS on everything                                         | **DON'T** run services on HTTP even internally (enables downgrade attacks)                   |
| **DO** enable 2FA/MFA on the auth gateway                                      | **DON'T** rely on single-factor authentication for internet-facing access                    |
| **DO** use Docker networks to isolate services                                 | **DON'T** put all containers on `bridge` or `host` networking                                |
| **DO** implement rate limiting on auth endpoints                               | **DON'T** skip firewall rules — use `ufw`/`iptables` to block everything except ports 80/443 |
| **DO** keep everything updated (proxy, auth service, OS, Docker)               | **DON'T** run services as `root` — use non-root users                                        |
| **DO** monitor access logs and set up alerts                                   | **DON'T** ignore failed login attempts — they signal brute-force attempts                    |
| **DO** consider Cloudflare Tunnel or Tailscale Funnel as an _additional_ layer | **DON'T** assume one solution is enough — defence in depth                                   |

---

## 📋 Deployment Requirements

| Requirement           | Minimum                               | Recommended                                           |
| --------------------- | ------------------------------------- | ----------------------------------------------------- |
| **Hardware**          | 2 CPU, 2 GB RAM                       | 4 CPU, 4+ GB RAM (Authentik is heavier)               |
| **OS**                | Any Linux (Debian/Ubuntu recommended) | Ubuntu 24.04 LTS or Debian 12                         |
| **Container runtime** | Docker + Docker Compose               | Docker Compose v2 (built into Docker)                 |
| **DNS**               | Dynamic DNS (if no static IP)         | Own domain name                                       |
| **SSL/TLS**           | Let's Encrypt (auto with Caddy)       | Let's Encrypt with HTTP-01 or DNS-01 challenge        |
| **Network**           | Ports 80 + 443 open inbound           | Consider Cloudflare Tunnel to avoid opening any ports |
| **Backup**            | None (not recommended)                | Regular backups of config files and auth database     |

---

## 🏆 My Recommendation for Your Situation

Given you already have Tailscale working and just want an auth front-end for services outside the mesh:

**Go with Authelia + Nginx (or Nginx Proxy Manager).** Here's why:

1. **Lightweight** — minimal resource overhead on your home server
2. **One login for all services** — Open Notebook, Open WebUI, everything gets protected uniformly
3. **Well-documented** — official integration guides for every major reverse proxy
4. **Low complexity** — single YAML config, easy to debug
5. **MFA built in** — TOTP, WebAuthn, Duo support

If you later want more power (granular policies, multiple auth backends, SSO with external providers), you can migrate to Authentik without changing your reverse proxy setup.

---

Would you like me to create a concrete Docker Compose deployment plan for Authelia + Nginx, or would you prefer to explore one of the other options in more depth?

I've saved your setup context to memory. Here's a summary of what's above:

**4 viable open-source options** ranked by fit for your situation:

1. **Authelia** — lightweight auth gateway (my top pick for you)
2. **Authentik** — full identity provider (more powerful, more complex)
3. **Pangolin** — WireGuard-native with built-in reverse proxy (only option actually using WireGuard)
4. **Caddy** — minimal stack with built-in auth (quickest to deploy)

Plus security considerations, DOs/DON'Ts, and deployment requirements all laid out above. Want me to create a concrete deployment plan for your preferred option?
