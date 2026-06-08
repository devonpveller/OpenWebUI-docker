# PLAN — WSL2 Memory & Resource Governance for ai-stack

**Status:** DRAFTED, not yet applied. Created 2026-06-08.
**Trigger:** "vmmemwsl memory grew ~25 GB overnight, host at 75% RAM" worry as the
platform scales. Investigation showed it was **reclaimable page cache, not a
leak** (see memory note `wsl-memory-reclaimable-cache`), but it exposed a real
gap: no host-side governance of the WSL2 VM.

Host: **128 GB RAM, 16 logical CPUs**, single shared WSL2 utility VM (Ubuntu +
docker-desktop, one `vmmemwsl`).

---

## 1. Background — what we learned

- `docker stats` MemUsage **counts reclaimable file cache**, so it overstates
  real usage. Ground truth:
  - per container: `docker exec <c> sh -c "cat /sys/fs/cgroup/memory.stat | grep -E '^(anon|file) '"`
    (`anon` = real/leaked, `file` = reclaimable cache)
  - whole VM: `wsl -e free -h` → read **available**, not "used".
- The overnight jump = the nightly **2 AM `openwebui-backup`** cron `tar`-ing the
  ~37 GB `openwebui-data` volume, filling page cache. Not the Quartz rebuild.
- Dropping caches frees memory **inside** Linux but `vmmemwsl` does **not** return
  the pages to Windows without `autoMemoryReclaim` or a `wsl --shutdown`.

## 2. Already done (2026-06-08, live, non-disruptive)

- [x] Dropped page cache once via a throwaway privileged container
  (`docker run --rm --privileged alpine:3.21 sh -c "sync; echo 1 > /proc/sys/vm/drop_caches"`).
  Freed ~38 GB inside the VM (free 2.4 GiB → 40 GiB).
- [x] Capped `openwebui-backup` cgroup memory to **1 GB** so the nightly tar
  can't balloon the VM. Persisted in `docker-compose.yml`
  (`deploy.resources.limits.memory: 1g`) and applied live with
  `docker update --memory 1g --memory-swap 1g openwebui-backup` (no restart).

## 3. The deferred change — apply `~/.wslconfig`

A drafted config already exists at **`C:\Users\yamao\.wslconfig`** (write-only,
NOT yet applied). It sets:

| Setting | Value | Why |
|---|---|---|
| `[wsl2] memory` | `96GB` | Hard cap; leaves ~32 GB for Windows. |
| `[wsl2] swap` | `16GB` | Graceful spikes instead of OOM. |
| `[wsl2] processors` | `14` | Leave 2 cores for Windows (optional/tunable). |
| `[experimental] autoMemoryReclaim` | `gradual` | **Return freed RAM to Windows** automatically — the actual fix for the symptom. |
| `[experimental] sparseVhd` | `true` | Let the ext4 .vhdx shrink on disk. |

### Why it's deferred
Taking effect requires `wsl --shutdown`, which **stops every container in BOTH
compose projects** (ai-stack + open-brain). Must be done in a maintenance window.

### Application procedure (maintenance window)
1. Announce/expect downtime. Stop OB1 first, then the main stack (reverse of
   startup order — OB1 depends on the main stack's `ai-stack_llm-net`):
   - `docker compose -f OB1/docker/docker-compose.yml down`
   - `docker compose down`
2. `wsl --shutdown` (from PowerShell). Wait ~10 s.
3. Bring WSL/Docker Desktop back up (Docker Desktop auto-starts the VM).
4. Start the main stack, **wait for `llama-cpp` healthy**, then OB1:
   - `docker compose up -d`
   - (wait for llama-cpp health) then
     `docker compose -f OB1/docker/docker-compose.yml up -d`
   - Or use `scripts/emergency-recovery.ps1` which encodes the ordered bring-up.
5. Verify (acceptance criteria below).

> NOTE: `scripts/emergency-recovery.ps1` already knows the correct shutdown/startup
> ordering across both projects — prefer it over manual `up/down` if unsure.

### Acceptance criteria (post-apply)
- `wsl -e free -h` total reflects the cap (~90 Gi, not ~62 Gi).
- After an idle period, Windows-reported RAM **drops** following a cache build-up
  (proves `autoMemoryReclaim` is returning pages) — was previously sticky.
- All health checks pass: `scripts/check-openbrain-health.ps1` (OB) + main stack
  containers healthy; `llama-cpp` healthy before OB1 came up.
- Tonight's 2 AM backup completes and `openwebui-backup` stays ≤ 1 GB.

### Rollback
Delete or rename `C:\Users\yamao\.wslconfig` and `wsl --shutdown` again. No
in-VM/data changes are involved; this only governs the VM envelope.

## 4. Tuning notes
- If `autoMemoryReclaim=gradual` doesn't reclaim fast enough for comfort, switch
  to `dropcache` (more aggressive).
- `memory=96GB` is conservative-generous; lower it (e.g. 64–80 GB) if Windows
  needs more headroom, or raise it as the stack genuinely grows. Watch the **real**
  signal (`anon` + `free` available), not `docker stats`.
- `processors=14` is optional; remove the line to give the VM all 16.

## 5. Future / optional resource-governance backlog
- [ ] **Audit per-container memory limits.** Most services have none
  (`HostConfig.Memory=0`). Add `deploy.resources.limits.memory` to the few that
  can spike (backups done; consider others reading large volumes). Bounds blast
  radius and keeps cache attribution honest.
- [ ] **Disk growth on `openwebui-data` (~37 GB uncompressed).** Separate from
  RAM. The backup retains 2 tarballs; watch `.vhdx` and `./backups/openwebui`
  size. `sparseVhd=true` helps but doesn't compact automatically — periodic
  `wsl --shutdown` + `Optimize-VHD` / `diskpart compact` may be needed.
- [ ] **Right monitoring signal.** Any memory alerting should read cgroup `anon`
  and `free` **available**, not `docker stats` MemUsage (false alarms from cache).
- [ ] **Consider a periodic idle cache-drop** only if `autoMemoryReclaim` proves
  insufficient — generally unnecessary once reclaim is on.

## 6. References
- Memory: `wsl-memory-reclaimable-cache` (diagnosis + commands)
- Memory: `openbrain-mcpo-ext-cpu-spin` (the earlier vmmemwsl CPU red herring)
- `scripts/emergency-recovery.ps1` — ordered cross-project restart
- `scripts/check-openbrain-health.ps1` — OB health/repair probe
- `.claude/skills/stack-map/` — networks/ports/dependency order
