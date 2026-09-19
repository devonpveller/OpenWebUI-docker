# Findings — little-coder restore (F1), 2026-08-29

The anchor's `findings_sink`. Checked against `work/lc-restore` at `eacb5f3`.

---

## F1a — coverage checking is not restorability checking

`scripts/checks/check-backup-coverage.ps1` reported `[OK]` for all five
little-coder volumes for as long as this bug existed. It was not wrong: a backup
container *does* reference those volumes, and it *does* produce fresh archives with
verified sentinels. What it cannot see is that nothing could read them back.

`scripts/sysadmin-mcp/check_backups.py` has the same blind spot from the other end — it
checks that an artifact is recent, not that it is usable.

So the stack had three independent green signals over an unusable restore path:
coverage OK, freshness OK, sentinel verifies. The one thing nobody ran was the restore.

**Worth its own item:** a periodic restore DRILL — pick a service, restore its newest
archive into throwaway volumes, assert the content matches, throw them away. It is the
only check that would have caught this, and the same drill would cover every other
catalog entry, several of which have never been exercised either (see F1b).

**Deliberately not built here.** It is a new check with its own cadence, alerting and
failure semantics; folding it into a catalog fix would be the wrong shape.

---

## F1b — the rest of the restore catalog is unexercised

While verifying this fix I looked at the other entries. None of the following has any
evidence of ever having been run, and each has a shape that could fail the same way:

- `openbrain-db` (`pg-restore`) needs `OB1_PG_PASSWORD` set from `OB1/docker/.env`, and
  errors out if it is missing. Fine — but nothing tells an operator that until mid-restore.
- `open-notebook` (`surreal-import`) targets `open_notebook`; the surreal import path is
  the most bespoke type in the file.
- `agent-bridge-db` and `mattermost-db` are **not in the catalog at all**, despite having
  working nightly pg_dump sidecars and being the authoritative store of every effort,
  gate and audit event in the org. Their backups exist; the documented restore does not.

That last one is the most serious of the three and is a direct sibling of this bug: a
backup with no restore path. It is out of scope here (this item is the little-coder
catalog entry) but it should not wait long.

---

## F1c — the producer was left alone, on purpose

The obvious-looking fix is to change `backup/little-coder-backup.sh` to emit five
per-volume archives so the original catalog patterns match. That was **rejected**: every
archive already on disk and on the NAS is the combined shape, so changing the producer
would leave the entire existing backup history unrestorable — which is this bug, not its
fix.

If per-volume archives are wanted later (they are the house pattern, and
`ao-worker-*-journals-backup` follows it), that change must be ADDITIVE: keep
`volume-tar-subdir` for historical archives and add per-volume entries for new ones. The
catalog already supports several archives per service, so this costs nothing structurally.

---

## F1d — `$matches` is an automatic variable (cosmetic, pre-existing)

`restore-from-snapshot.ps1` assigns to `$matches` in the discovery loop, which PowerShell
uses as the automatic regex-capture variable. PSScriptAnalyzer flags it
(`PSAvoidAssignmentToAutomaticVariable`). It works here because nothing reads the regex
captures afterwards, and the `-match` on the line above happens to populate the same
variable it then overwrites — which is exactly the kind of coincidence that stops being
true after an edit.

Not renamed here: this item's changes are already in that function, and renaming a
variable in the same commit as a behavioural fix makes both harder to review. Worth a
one-line follow-up.
