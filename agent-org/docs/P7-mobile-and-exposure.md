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

**Two gotchas (both handled below):**
1. **Socket path.** This stack's `tailscaled` listens on **`/tmp/tailscaled.sock`**, not the
   default `/var/run/tailscale/tailscaled.sock` — you MUST pass `--socket=/tmp/tailscaled.sock`
   or you get *"Failed to connect to local Tailscale daemon … not running?"*.
2. **Reachability.** The `tailscale` container shares **openwebui's** netns (on `ai-stack_llm-net`),
   so `127.0.0.1:8065` inside it is *not* Mattermost. The compose now also attaches `mattermost`
   to `llm-net`, so `tailscale serve` reaches it **by container name**: `http://mattermost:8065`.
   (Recreate `mattermost` once to pick up the new network — see below.)

```bash
# 0) one-time: recreate mattermost so it joins llm-net (compose change already applied).
docker compose -f agent-org/docker/docker-compose.yml up -d mattermost

# 1) serve it on the tailnet (pick a FREE port; 8443/8444/8445 are taken — see the stack-map).
docker exec tailscale tailscale --socket=/tmp/tailscaled.sock \
  serve --bg --https 8446 http://mattermost:8065

# verify:
docker exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status
```

Then set `MM_SITE_URL` in `agent-org/docker/.env` to the tailnet URL
(`https://openwebui.<tailnet>.ts.net:8446`) and recreate `mattermost` again to apply it.

*Done-when:* Mattermost is reachable on the tailnet only (not public) and agent channels are
non-E2EE.

### OD-9 — mobile push privacy (decide at P7)
Mattermost mobile push uses either the public **HPNS relay** (leaks notification metadata
off-box) or a **self-hosted push proxy**, or neither. For this stack's privacy posture, prefer
**self-hosted push proxy** or **tailnet-only manual** (open the app to see). Recommended v1:
tailnet-only manual; revisit if push latency matters.
