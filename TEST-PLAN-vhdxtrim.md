# Test plan — `vhdxtrim` (branch `work/vhdxtrim`, commit 22369a1)

For a tester who did not write this. Everything below runs **read-only against the live stack**:
no compaction, no volume removal, no container restart. Nothing here needs a plane lease.

Run from the worktree: `D:\Open WebUI\ai-stack\.claude\worktrees\wt-vhdxtrim`

> **The developer could not run two things.** The auto-mode permission classifier blocked every
> destructive docker call (`reclaim_execute`, `docker image prune`, `docker volume rm`) and even
> the read-only `docker images -f dangling=true`. If it blocks you too, record that as BLOCKED —
> do not work around it, and do not pass T6 on reasoning alone.

---

## T1 — the whole suite is green

```powershell
cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-vhdxtrim\scripts\sysadmin-mcp"
python test_sysadmin.py      # expect: 36 passed, 0 failed
python test_compaction.py    # expect: 42 passed, 0 failed
python test_executor.py      # expect: 24 passed, 0 failed
powershell -NoProfile -ExecutionPolicy Bypass -File .\test-compact-lib.ps1   # expect: passed 17, failed 0
ruff check .                 # expect: All checks passed!
```

**PASS** all four green and ruff clean. **FAIL** any failure, any error, or a count *lower* than
stated (a lower count means tests vanished, which is the failure this repo keeps hitting).

## T2 — the new tests can actually go RED

A green test proves nothing until you have seen it fail. Drive the verdict library against a
stub with the OLD always-ok behaviour:

```powershell
# write a stub exporting Get-ReclaimVerdict that ALWAYS returns ok=$true
powershell -File .\test-compact-lib.ps1 -Lib <path-to-your-stub>
```

**PASS** exit code 1, with **case 1 ("2026-09-13 regression … is NOT ok") among the failures**.
**FAIL** it still passes, or it fails only on cases unrelated to the shortfall verdict.

Do the same for the ordering guard: copy `compact-vhdx.ps1`, move the `/sbin/fstrim` block to
*after* the `wsl --shutdown 2>&1` line, point `test_compaction.py`'s `_HERE` at the copy (or edit
the copy in place in a scratch dir), and confirm **"ps trims BEFORE wsl --shutdown"** goes red.
**FAIL** if a trim moved below the shutdown still passes — that check would then be decorative.

## T3 — fstrim ordering is real, not just asserted

Read [scripts/sysadmin-mcp/compact-vhdx.ps1](scripts/sysadmin-mcp/compact-vhdx.ps1) and confirm by eye:

1. The `/sbin/fstrim` call sits **before** `& $docker desktop stop` and before `wsl --shutdown`.
2. It is reached on the normal path — not inside the `catch` of the trapped-space probe, and not
   behind a condition that is false by default. (`-SkipFstrim` defaults to off; that is fine.)
3. Failure of fstrim does **not** abort the run — it sets `fstrim_ok=$false` and continues.

**PASS** all three. **FAIL** any one. Note 2 is the one that matters: a trim that only runs when
some earlier step succeeded would silently revert to the old behaviour.

## T4 — the shortfall verdict matches the real incident

```powershell
cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-vhdxtrim\scripts\sysadmin-mcp"
. .\compact-lib.ps1
Get-ReclaimVerdict -TrappedGb 54.4 -ReclaimedGb 9.9 -FstrimOk $null   # the real 2026-09-13 run
Get-ReclaimVerdict -TrappedGb 47.8 -ReclaimedGb 44.0 -FstrimOk $true  # the run we expect next
```

**PASS** the first returns `ok=False`, `shortfall_gb=44.5`, `floor_gb=27.2`, and a reason naming
the missing trim; the second returns `ok=True`. **FAIL** otherwise.

Cross-check the numbers against `scripts/sysadmin-mcp/state/compact-result.json` on the **main**
checkout — the notes array there must still show `trapped ~= 54.4` and `reclaimed 9.9`. If that
file has been overwritten by a newer run, say so; the claim then rests on the findings note only.

## T5 — threshold split behaves at the live value

```powershell
python -c "import sys; sys.path.insert(0,'.'); import compaction as cp; print(cp._min_trapped_gb()); print(cp.compact_plan()['warranted'], cp.compact_plan()['trapped_gb'])"
```

**PASS** the act threshold is `20.0`, and with live trapped space in the 40s `warranted` is
`True`. **FAIL** if the threshold still resolves to 60, or `warranted` is False while trapped
exceeds 20.

Also confirm `disk_report`'s WARN behaviour is unchanged: `vhdx_trapped_warn_gb` must still be
**60** in `config.json`. **FAIL** if this change quietly lowered the warn threshold too — the
point was to separate them, not to move both.

## T6 — volume classification against the REAL stack

```powershell
python -c "import sys; sys.path.insert(0,'.'); import sysadmin as sa, server as srv; print(srv.render_volume_report(sa.volume_report()))"
```

**PASS** `ai-stack_openwebui-data` appears under **COLD** with an age of roughly 23–24 days, and
the `DO_NOT_PRUNE` section is empty or contains only volumes younger than 14 days.
**FAIL** if `ai-stack_openwebui-data` is still listed as DO NOT PRUNE.

Then verify the conservative fallback is real, not claimed — make `_wsl_dd` fail and confirm
**nothing** is classified cold:

```powershell
python -c "import sys; sys.path.insert(0,'.'); import sysadmin as sa; sa._wsl_dd = lambda a,timeout=30: {'rc':1,'out':'','err':'x'}; r=sa.volume_report(); print(r['dangling_protected_cold']); print(len(r['dangling_protected_DO_NOT_PRUNE']))"
```

**PASS** cold list is `[]` and DO_NOT_PRUNE is non-empty. **FAIL** if a broken age probe causes
live volumes to be reported as orphan candidates — that is the dangerous direction.

## T7 — nothing here can delete anything

```powershell
git -C "D:\Open WebUI\ai-stack\.claude\worktrees\wt-vhdxtrim" show --stat 22369a1
git -C "D:\Open WebUI\ai-stack\.claude\worktrees\wt-vhdxtrim" diff development...work/vhdxtrim -- scripts/sysadmin-mcp/
```

**PASS** the diff contains no `docker volume rm`, no `volume prune`, no `Remove-Item`, and
`volume_report` still makes no mutating docker call. **FAIL** any destructive verb reaching a
code path — `compact-lib.ps1` in particular is dot-sourced into an **elevated** script.

## T8 — the findings note is held to the artifact's standard

Open [documentation/notes/vhdx-compaction-shortfall-2026-09-13.md](documentation/notes/vhdx-compaction-shortfall-2026-09-13.md)
and pick **any three** factual claims. Verify each against the code path or recorded number it
cites, not against a comment. Suggested, because these are the load-bearing ones:

- the mount has no `discard` option → `wsl -d docker-desktop -e sh -c "mount | grep docker-desktop-disk"`
- `e94a6d9` deleted the `code_agent` rows → `git show e94a6d9 --stat` and read the commit body
- "all 18 protected-dangling volumes are cold" → recount from T6's output

**PASS** all three check out. **FAIL** any claim that is overstated or unverifiable. An
unverified sink looks like a record and the next item acts on it.

---

## Out of scope for this test pass

- **Running a real compaction.** It needs elevation, operator-scheduled downtime and ~15 min. The
  fstrim fix is verified in principle (ordering + recorded outcome + verdict), **not in outcome**.
  The outcome proof is the next real run's `reclaimed` vs `trapped_before_gb`, and it has not
  happened. Do not pass or fail this plan on it — record it as the known open item.
- Dropping any volume; importing the May-2025 chats; the NAS archive copy.
- The absolute-GB alert thresholds from the 2026-09-06 `diskalert` finding.
