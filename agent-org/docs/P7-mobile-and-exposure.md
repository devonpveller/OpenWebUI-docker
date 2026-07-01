# P7 — Mobile + exposure (operator runbook)

Author-here, **operator-deployed** (🚀). The alignment core (P0–P6) does not depend on this;
P7 makes the org operable from your phone and pins the exposure posture. Governance §1/§3/§5/§7.

## P7.1 — Human-Operator mobile flow

1. Install the **Mattermost** app (iOS/Android).
2. Sign in as the **system admin** account created at P0.2 — the Human Operator is admin and can
   **join any channel/DM** (observability = safety; no E2EE on agent channels, §5/§7).
3. From the phone you can, in `#mgmt`:
   - **Decide a CONCERN** — reply `approve <effort_id>`, `modify <effort_id> <note>`, or
     `abort <effort_id>`. Only the Human Operator may clear a **hard-gate** CONCERN (§3
     invariant iv); the PO can clear steering CONCERNs from within the org.
   - **Trigger the global kill switch** — post `kill` (freezes every effort at once) / `unkill`.
   - Read/join any `#effort-*` channel to correct direction in real time.

*Done-when:* you approve a CONCERN and trigger the kill switch from the phone app.

## P7.3 — CONCERN UX

v1 ships **structured plain posts** (OD-5): the bridge posts a formatted CONCERN card-like
message to `#mgmt` (intent / what surfaced / why it matters / options with effect-on-outcome /
PM recommendation / blocked efforts) and parses your `approve|modify|abort <effort_id>` reply.

A richer **Mattermost plugin** (interactive buttons on the CONCERN post) is the P7.3 upgrade —
optional; the plain-post flow is fully functional without it. If you build the plugin, mount it
into the `mattermost-plugins` volume and enable it in System Console → Plugins.

## P7.4 — Tailnet exposure (🚀 operator) — NO public exposure

Mattermost is host-published on `127.0.0.1:8065` only. Expose it on the **tailnet** via
`tailscale serve` (mirrors the open_notebook `:8443` / wiki `:8444` pattern), **not** the
Cloudflared/Authelia portal (Mattermost Team Edition is dropping SSO; tailnet is simpler and
private — PLAN §3.7).

```bash
# The tailscale container shares openwebui's netns. Mattermost is on ao-net (a bridge), so
# expose it through the host loopback publish the compose file already provides:
docker exec tailscale tailscale serve --bg --https 8446 http://127.0.0.1:8065
#   (choose a free tailnet port; 8443/8444/8445 are already taken — see the stack-map)
```

Then set `MM_SITE_URL` in `agent-org/docker/.env` to the tailnet URL
(`https://openwebui.<tailnet>.ts.net:8446`) and recreate `mattermost`.

*Done-when:* Mattermost is reachable on the tailnet only (not public) and agent channels are
non-E2EE.

### OD-9 — mobile push privacy (decide at P7)
Mattermost mobile push uses either the public **HPNS relay** (leaks notification metadata
off-box) or a **self-hosted push proxy**, or neither. For this stack's privacy posture, prefer
**self-hosted push proxy** or **tailnet-only manual** (open the app to see). Recommended v1:
tailnet-only manual; revisit if push latency matters.
