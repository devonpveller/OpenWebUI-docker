# vhdx compaction returned 9.9 of 54.4 GB and reported success — 2026-09-13

Operator question: *"sysadmin just performed a compaction this morning but the process only saved
9 GB from a 221 GB vhdx, is this really all that could be retrieved?"* No. This note records what
was measured, by which command, and what was changed.

Anchor: `vhdxtrim`. Artifact: the `scripts/sysadmin-mcp/` patches this note accompanies.

---

## 1. The run under-delivered against its own measurement

`scripts/sysadmin-mcp/state/compact-result.json`, written by the run itself:

```
"03:15:03 trapped ~= 54.4 GB (vhdx 247 - used 192.6)"
"03:29:06 vhdx 247 -> 237.1 GB (reclaimed 9.9)"
"03:30:31 DONE ok=True C: 86.2 -> 90.3 GB"
```

It measured **54.4 GB** trapped, returned **9.9 GB**, and wrote `ok=true`. `disk_report` at
~07:50 still showed **47.8 GB trapped** (allocated 237.9, used inside 190.1).

**Cause.** `compact-vhdx.ps1` went from `wsl --shutdown` straight to `Optimize-VHD -Mode Full`
with no trim step. `Optimize-VHD` cannot read ext4; it reclaims only blocks the guest has already
discarded. The guest does not discard on its own — verified live:

```
$ wsl -d docker-desktop -e sh -c "mount | grep docker-desktop-disk"
/dev/sdd on /mnt/docker-desktop-disk type ext4 (rw,relatime)     # no `discard` option
```

So only what Docker Desktop's own periodic trim had happened to mark was eligible: 9.9 GB of it.
Running `fstrim` by hand at ~07:55 completed across the free extent (`813.8 GiB (873815588864
bytes) trimmed` — that is the size of the range discards were issued over, NOT proof of newly
freed blocks; the proof is the next compaction's number).

The vhdx was re-measured at **237.9 GB, unchanged**, after the manual fstrim — expected: WSL2
never shrinks the file itself, the trim only makes blocks eligible.

**Fixed by:** `fstrim` before the engine stop (it needs the distro up and the disk mounted, which
the trapped-space `df` immediately above it has just proven), `trapped_before_gb` / `fstrim_ok` /
`shortfall_gb` promoted to real result fields, and `Get-ReclaimVerdict` in the new
`compact-lib.ps1` failing the run when the return falls proportionally short AND misses by more
than the grace. `test-compact-lib.ps1` case 1 is the real 54.4/9.9 numbers.

## 2. One threshold was doing two unrelated jobs

`compaction.py::_min_trapped_gb()` read `thresholds.vhdx_trapped_warn_gb` — the same 60 GB number
`disk_report` uses to decide whether to *mention* compaction. At 47.8 GB trapped that meant
`disk_report` said **HEALTHY** and `compact_execute` **hard-refused**, so the trapped space was
unreachable from either direction. `compact-vhdx.ps1` meanwhile carried its own unrelated
`-MinTrappedGb 1.0`, so the script would have run happily; only the Python gate said no.

**Fixed by:** a separate `vhdx_compact_min_gb` (20), falling back to the warn number when absent.
Verified live after the change: `compact_plan` reports `trapped=44.8GB, min=20.0GB,
warranted=True` where it previously refused.

## 3. `volume_report` stamped DO NOT PRUNE on 15-month-cold data

`protected_volume_substrings` is a substring filter over the dangling set. `"openwebui"` matched
the dead `ai-stack_openwebui-data` exactly as strongly as any live volume, so the report said
**never prune** about data nothing had written since the day before the 2026-08-21 plane split.

Dangling already means no container references a volume (confirmed per-volume with
`docker ps -aq --filter volume=<name>` → 0 for all five orphans, stopped containers included).
Age is the missing second signal.

**Fixed by:** `_volume_last_write()` (one `find -maxdepth 3 … -exec stat` call) and a
`dangling_protected_cold` bucket. A failed wsl call yields no ages and therefore reclassifies
nothing — tested.

**Measured result:** of the 18 protected-dangling volumes, **all 18** are cold (≥14 days). The old
report's entire "never prune" list was orphan candidates. `DO_NOT_PRUNE` is now empty here.

---

## Findings NOT acted on (true, out of scope for this anchor)

- **`mnemory_mnemory-data` last written 146.5 days ago and `ai-stack_mnemory-data` 22.8 days ago,
  both dangling.** Neither is the live mnemory volume. Not investigated — the memory plane was
  out of scope. Worth an owner before either is touched.
- **`iks-dev_iks-db-data` / `iks-surreal-data` / `iks-notebook-data` dangling at 23.6–99 days.**
  Consistent with the iks-dev overlay having been torn down with volumes deliberately kept; this
  note does not re-decide that.
- **`ai-stack_openwebui_sessions`, `openwebui_config`, `ai-stack_tailscale_state` /
  `ai-stack_tailscale-state` cold at 420–472 days.** Both tailscale spellings are cold, so the
  live tailscale state lives elsewhere; not traced here.
- **D: free fell 553.9 → 515.6 GB between two readings ~20 min apart, before this session wrote
  anything to D:.** Not explained. Re-measured stable at 515.6 afterwards. Flagging only.
- **`reclaim_execute` could not be run**: blocked by the Claude Code auto-mode permission
  classifier on three attempts, including the read-only `docker images -f dangling=true` probe.
  An environment/permission matter, not a defect in this code. The ~34 GB of dangling images and
  build cache remain unreclaimed at the time of writing.
- **`docker system df` reports 63.2 GB reclaimable across 765 dangling volumes**, of which the top
  named orphans account for ~16 GiB. The 738 anonymous hex volumes were not individually sized
  beyond confirming only one exceeds 391 MiB.

## Orphan volume triage (the five that were backed up)

Backed up to `backup/orphan-volumes-2026-09-13/` (gitignored), each verified readable —
`tar -tf` returned rc=0 with its database present.

| volume | size | alembic head | verdict |
|---|---|---|---|
| `ai-stack_openwebui-data` | 10.0 GiB | `f0bd01a18a3d` — **same as live** | strict subset of live; drop |
| `ai-stack_llm-gateway-db-data` | 0.8 GiB | postgres, superseded by `inference_llm-gateway-db-data` | drop |
| `openwebui_data` | 1.9 GiB | `9f0c9cd09105` (24 tables vs live's 44) | incompatible; keep separated |
| `ai-stack_openwebui_data` | 1.9 GiB | `9f0c9cd09105` | incompatible; keep separated |
| `open-webui` | 1.3 GiB | `9f0c9cd09105` | incompatible; keep separated |

The Aug-20 volume's only content absent from live is the function `code_agent` and the tool
`code_agent_tools` — deliberately deleted by commit `e94a6d9` (K.8, 2026-08-21): *"live webui.db
rows deleted (pipe was inactive)"*. Nothing was lost in the plane split. Every other id compared
(chat, model, note, knowledge) is a subset of live.

The three May-2025 volumes are ~99% redownloadable HuggingFace embedding cache (`uploads` empty in
all three). Their real content is 30 chats dated 2025-05-20 → 05-30, exported to
`backup/orphan-volumes-2026-09-13/may2025-chats-export.json` so the data is importable even though
the schema is not mergeable.
