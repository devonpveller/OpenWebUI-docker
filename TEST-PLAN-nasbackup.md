# Test plan — `nasbackup` (branch `work/nasbackup`, commit 6d6cd71)

For a tester who did not write this. T1–T6 are read-only and need no lease. **T7 touches
the NAS and needs a `frontend`-free quiet window**; read its warning first.

Run from `D:\Open WebUI\ai-stack\.claude\worktrees\wt-nasbackup`.

> **Secret handling.** `.env` in this worktree contains the live NAS password. Do not paste
> `.env` contents, log output containing a password, or the value itself into any evidence
> file, Mattermost thread or commit. Evidence should say `<redacted>`.

---

## T1 — tests green, lint clean

```powershell
python scripts\sysadmin-mcp\test_check_backups.py     # expect: 11 passed, 0 failed
python scripts\sysadmin-mcp\test_sysadmin.py --unit   # expect: 10 passed, 0 failed
python scripts\sysadmin-mcp\test_compaction.py        # expect: unchanged from development
ruff check scripts\sysadmin-mcp\
powershell -NoProfile -File - <<'EOF'
Get-ChildItem scripts\backup\*.ps1 | ForEach-Object { $e=$null
  $null=[System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw $_.FullName),[ref]$e)
  if($e.Count){"$($_.Name) FAIL"}else{"$($_.Name) OK"} }
EOF
```

**PASS** all green, all four `.ps1` parse OK. **FAIL** anything red or a *lower* test count.

## T2 — the off-site check can go RED, and mtime alone could not have caught it

This is the load-bearing one. Edit a copy of `check_backups.py` so `_offsite_status()`
ignores `_NAS_SUCCESS_MARKER` and judges on age only, then re-run `test_check_backups.py`.

**PASS** the case **"fresh but FAILED sync is stale"** goes red, and so does
**"a newer FAILED log beats an older good one"**. **FAIL** if an age-only implementation
still passes the suite — the suite would then not be testing the actual regression.

## T3 — it flags the REAL outage, against the real logs

```powershell
python -c "import sys; sys.path.insert(0,'scripts/sysadmin-mcp'); import check_backups as cb; cb._REPO_ROOT=r'D:\Open WebUI\ai-stack'; print(cb._offsite_status())"
```

That points the checker at the **main checkout's** real `logs/`.

**PASS** it returns a non-empty dict. If the 2026-09-13 sync has since completed
(it was running at hand-off), the correct result is `{}` and you must instead verify
against the preserved failure by pointing it at a copy of `logs/nas-sync-2026-09-06.log`
in a temp dir — **say in your evidence which of the two you did.**
**FAIL** if a log whose last `[ERROR]` is `net use failed` is reported healthy.

## T4 — credential precedence, both directions

```powershell
# .env present -> used
python -  # or read the log line from a -DryRun run
```
Run `scripts\backup\backup-to-nas.ps1 -NasUncRoot "\\192.168.1.247\backups\ai-stack\portal" -DryRun`
from this worktree. It will **abort at "backup source is empty"** — that is correct and
expected here (the worktree has no `backups/`), and it happens *after* the credential load,
so the log still proves which source was used.

**PASS** the log says `credentials loaded from .env (user: backup-user)`, and the password
never appears in `logs\nas-sync-*.log` (grep the log for it — expect **0** hits).
Then temporarily rename the two `NAS_BACKUP_*` keys in this worktree's `.env` and re-run:
**PASS** it falls back with `no NAS_BACKUP_* in .env; falling back to DPAPI vault`.
**FAIL** if removing the `.env` keys breaks a run the vault could have served, or if the
password appears anywhere in the log.

## T5 — failure diagnostics are named, not generic

Read the `if ($netExit -ne 0)` block in `scripts\backup\backup-to-nas.ps1`. Confirm by eye
that 2242, 1219, 1326 and 53 each produce a distinct, actionable message, and that
`Send-AlerterFailure` is called with the *diagnosed* reason rather than the generic one.

**PASS** all four named and the diagnosed reason forwarded. **FAIL** a bare
`net use failed (exit 2)` remaining the only output for 2242 or 1219.

## T6 — escalation actually fans out

Read `Send-AlerterFailure`. Confirm the Mattermost/Telegram attempts are **outside** the
`if ($result.Ok)` branch — i.e. they run regardless of the alerter's result — and that
`$delivered` / `$failed` are both logged.

Then exercise it for real without breaking anything: temporarily point `$mmPost` at a
script that exits 1 and confirm the log records it under "alert channels that failed"
while other channels still report.

**PASS** one channel failing never short-circuits the others, and an all-down run logs
`ALERT UNDELIVERABLE`. **FAIL** if any single failure can end the escalation — that is
precisely the shape of this outage and the reason the anchor exists.

## T7 — a real sync completes (DESTRUCTIVE — read first)

A run was started at 2026-09-13T10:39 and was still copying a 135.8 GB `lm-models`
tarball at hand-off. **Check whether it finished before starting another.**

```powershell
Select-String "NAS sync complete" "D:\Open WebUI\ai-stack\logs\nas-sync-2026-09-13.log"
```

**PASS** if that marker is present AND the NAS shows slot-B newer than 2026-08-30:

```powershell
net use \\192.168.1.247\backups <pass> /user:backup-user /persistent:no
Get-ChildItem \\192.168.1.247\backups\ai-stack\portal | Select Name,LastWriteTime
net use \\192.168.1.247\backups /delete /yes
```

If it did **not** finish, re-running is `robocopy /MIR` — it **deletes destination
extras** (120 files / ~143 GB on the 09-13 dry run). Do not re-run it casually; confirm
with the operator first. **FAIL** the plan only if the sync errored; an incomplete
long-running copy is a WAIT, not a failure.

---

## Out of scope

- Repairing the portal-alerter's OAuth grant (Google Cloud, operator action).
- Changing what `/health` returns — the killswitch consumes it.
- The other single-channel `Send-PortalAlert` callers (watchers, tripwire, cron).
- `install-nas-backup-task.ps1` still registers the hostname UNC; the in-script IP
  resolution covers it, but the task argument itself was not changed.
