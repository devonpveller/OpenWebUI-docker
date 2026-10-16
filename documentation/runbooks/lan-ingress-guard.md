# LAN ingress guard - the firewall in front of LAN-published ports

> Status: BUILT, NOT APPLIED. Created 2026-10-16 (harness item `edge-b1-fw`,
> cluster-transition PLAN section 5.1, Track B item B1). Applying it on the
> workstation is an operator step in the cutover window.

## What it protects

Every ai-stack web port publishes on `127.0.0.1` today. For the Proxmox nodes'
Traefik to front a service, its compose file has to publish it on the
workstation's LAN address too (Track B item B2). **Docker Desktop's own inbound
firewall rule (`com.docker.backend`: Allow, any remote address, any port) then
opens that port to the whole LAN.** Some of those services have no login of
their own, so a LAN host could reach them without going through Authentik.

The guard is one inbound Windows Firewall **BLOCK** rule per listed port. Its
remote addresses are everything EXCEPT the Traefik nodes: every other IPv4
address and the whole IPv6 space. A block beats an allow in Windows Firewall,
so Docker's rule is left alone and simply loses. Each rule covers the port on
every local address (not only the LAN IP), so a port that ends up published on
`0.0.0.0` or on an IPv6 address is covered too.

| | |
|---|---|
| Port list (tracked) | `stack.manifest.toml` `[lan_ingress.ports]`: one line per service |
| Addresses (per host, gitignored) | `.stack/lan-ingress.toml` beside the manifest: `host_ip` (the workstation's LAN IPv4) and `allowed_remote` (the Traefik nodes). Template: `scripts/network/lan-ingress.host.example.toml` |
| Script | `scripts/network/lan-ingress-firewall.ps1` (PowerShell 5.1) |
| Rules it owns | names starting `ai-stack-lan-guard-` (one per port: `ai-stack-lan-guard-<service>-<port>`), group `ai-stack LAN ingress guard` |
| Test | `powershell -NoProfile -File scripts\network\test-lan-ingress-firewall.ps1` (firewall faked; refuses to run elevated) |

Today's ports: Open WebUI 3000, Mattermost 8065, LiteLLM UI 8445, wiki-viewer
8812, workbench 8814.

**Why the addresses are in a separate file.** This repository is public, and
`scripts/checks/check_identity.py` keeps LAN (RFC 1918) addresses out of
everything it tracks. Each fact still has one home: the ports in the manifest,
the addresses in `.stack/lan-ingress.toml` (the same per-host, gitignored
directory as `.stack/state.json`). If either is missing or invalid, or a key is
set in both, the script aborts and changes nothing. Below, `<host_ip>` and
`<node>` stand for the values in that file.

## The order: guard first, then publish

0. **Once per host - write the address file** (no elevation needed):

   ```powershell
   Copy-Item scripts\network\lan-ingress.host.example.toml .stack\lan-ingress.toml
   notepad .stack\lan-ingress.toml     # set host_ip and allowed_remote to this LAN's real values
   ```

   Run it from the checkout the operator deploys from (the main checkout).
   A worktree has no `.stack/`. The file is not in git and not in the nightly
   config backup (see the item's findings), so keep a note of its values.

1. **B1 - apply the guard** (elevated PowerShell, from the repo root):

   ```powershell
   powershell -NoProfile -File scripts\network\lan-ingress-firewall.ps1 -Apply -WhatIf   # read the plan first
   powershell -NoProfile -File scripts\network\lan-ingress-firewall.ps1 -Apply
   powershell -NoProfile -File scripts\network\lan-ingress-firewall.ps1 -Status         # expect every rule OK, exit 0
   ```

   `-Apply` refuses without elevation and changes nothing. It needs Python
   3.11+ on PATH (it reads the manifest with `tomllib`); if Python, the
   manifest table or the address file is missing or invalid, it aborts and
   changes nothing.

2. **B2 - publish the ports** on `<host_ip>` (the compose changes) and recreate
   the services the way that item says.

3. **Verify** (below), with `-Status` first: every listed port that listens on the LAN IP
   or on all addresses must say `guarded`. `EXPOSED` means a port is reachable
   and its rule is missing or drifted. Stop and run `-Apply`.

Never the other way round. A port published before its rule exists is open to
the LAN for that time. To take a port away again: unpublish it first, then
remove its line from `[lan_ingress.ports]` and `-Apply` (the stale rule is
removed).

## Verify from a host that is NOT a Traefik node

From any LAN machine that is not one of the `allowed_remote` nodes (a laptop
or a phone on the same Wi-Fi):

```powershell
# Windows
Test-NetConnection <host_ip> -Port 3000        # expect TcpTestSucceeded : False
```

```sh
# Linux / macOS
curl -m 5 -sS -o /dev/null -w '%{http_code}\n' http://<host_ip>:3000/   # expect a timeout, not a code
nc -vz -w 5 <host_ip> 8065                                               # expect a timeout
```

Repeat for 8065, 8445, 8812 and 8814. Then from a Traefik node (`<node>`), which must
connect:

```sh
curl -m 5 -sS -o /dev/null -w '%{http_code}\n' http://<host_ip>:3000/   # expect 200 (or a redirect code)
```

And on the workstation itself, `http://127.0.0.1:3000/` must still work. Windows
Firewall does not filter loopback traffic, so the guard does not apply to it.
If localhost breaks, roll back and report it.

If the LAN has IPv6, also try the workstation's IPv6 address from the non-node
host (`ipconfig` shows it). It must fail too.

## Roll back

First unpublish the LAN ports (revert B2). Otherwise, once the rules are gone,
Docker's allow rule opens those ports to the whole LAN again. Then:

```powershell
powershell -NoProfile -File scripts\network\lan-ingress-firewall.ps1 -Remove -WhatIf   # lists exactly the guard's rules
powershell -NoProfile -File scripts\network\lan-ingress-firewall.ps1 -Remove           # elevated
```

`-Remove` deletes only rules whose name starts with `ai-stack-lan-guard-`. It
does not read the manifest or the address file, so it still works if either one
is broken.

## Adding a service later

One line in `[lan_ingress.ports]`
(`<service> = { plane = "<plane>", port = <port> }`), then `-Apply` (elevated),
then publish it. Never add an address to `allowed_remote` for a test. That list is the
whole allow set. The service-lifecycle checklist points here
([SERVICE-LIFECYCLE.md](SERVICE-LIFECYCLE.md), "When you EXPOSE a service").

## What the script refuses

It aborts with no change when:

- the manifest, its `[lan_ingress]` table or the address file cannot be read;
- either file has a key or table it does not know, the address file sets
  `ports`, or a key is set in both files;
- `allowed_remote` is empty, or any entry is not a plain dotted-quad IPv4
  address. CIDR, ranges, wildcards, keywords, IPv6 and leading zeros are all
  refused, because a mis-parsed entry could widen the allow set without anyone
  noticing;
- `ports` is empty, a port is not an integer in 1-65535, a port is listed twice,
  or a plane is not declared in the manifest.

It never edits or disables Docker's rules, and it never touches a rule outside
its prefix.

## Exit codes

`0` ok; `1` aborted (bad source or a failed step: it prints what it already did);
`2` refused, not elevated; `3` `-Status` found MISSING / DRIFT / STALE rules or
an EXPOSED port.
