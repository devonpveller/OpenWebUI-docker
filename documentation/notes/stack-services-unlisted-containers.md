# Finding — containers still absent from `scripts/lib/stack-services.json` (2026-08-30)

agent-org's absence from this file was found, written down, and sat unowned long enough that
C.6 cites it as the proof its overflow-queue rule was needed. It is now fixed: 16 rows
(6 always-on, 8 profile-gated, 2 backup sidecars), reconciled against the compose render in
both directions.

Checking the reverse direction — running containers named nowhere in the file — surfaced
more. They are recorded here rather than folded into that fix, because they are separate
orphans with separate owners.

## The Portal — an entire project, 12 containers

`caddy`, `authelia`, `cloudflared`, `authelia-watcher`, `authelia-notif-bridge`,
`tunnel-watcher`, `integrity-tripwire`, `portal-alerter`, `portal-cron`, `caddy-backup`,
`authelia-backup`.

This is the **internet-exposed** front door, which makes it the most consequential absence:
the sysadmin plane's `stack_health` and the watchdog's repair targets both read this file, so
today neither can see the containers that terminate public traffic. It has no `projects[]`
entry either, so there is nothing for rows to point at.

`portal/docker-compose.yml` is driven by `scripts/portal/portal-on.ps1` / `portal-off.ps1`
and is deliberately not part of the recovery script's ordered restart. Whether it should be
health-monitored the same way is a judgement for the operator, not a mechanical gap-fill —
which is exactly why this is a note and not a commit.

## Others

| Container | Looks like |
|---|---|
| `stt-tts-server`, `stt-tts-tailscale` | A speech project outside every declared compose file. |
| `task-management-api-1`, `task-management-db-1` | Docker-Compose default naming (`-1` suffix), so an unnamed project — possibly not this workspace's at all. |
| `openbrain-idea-refinery` | IS in the OB1 compose under the `idea-refinery` profile; the plane rows do not carry a `profile` key for it the way the new agent-org rows now do. |

## Why this matters beyond tidiness

A container missing from this file is invisible to the sysadmin MCP's `stack_health` and to
`check-watchdog-repair-targets.ps1`. That check passes at 24 targets today — a number that
reads as coverage and is really just the size of the list. Nothing compares the list against
what is actually running, which is why the gap survived: the check is honest about what it
checks and nobody asked it the other question.

**Worth building:** a both-directions reconciliation, the same shape the initdb-chain check
already has (every mount names a real file; every file is mounted). One direction alone is
what let this sit.
