# Findings — memory-plane Phase 0 (U1), 2026-08-29

The anchor's `findings_sink`. True problems found while building Phase 0 that
belong to OTHER work: not deleted, not smuggled into this deliverable. Each
records what was checked and when, so the next person does not re-derive it.

Checked against `work/dfu-mem0` at `d7d1676` (worktree `wt-dfu-mem0`),
2026-08-29.

---

## F1 — `little-coder`'s restore catalog cannot find its own archives

**Severity: real, and it only surfaces during a restore — i.e. the worst
moment to discover it.**

`backup/little-coder-backup.sh:24` writes ONE archive:

    ${BACKUP_DIR}/little-coder-backup-${TIMESTAMP}.tar.gz

`scripts/backup/restore-from-snapshot.ps1:111-118` looks for FIVE, by
per-volume prefix:

    little-coder-journals-*.tar.gz  -> coder_little-coder-journals
    little-coder-skill-*.tar.gz     -> coder_little-coder-skill
    little-coder-cohorts-*.tar.gz   -> coder_little-coder-cohorts
    little-coder-polyglot-*.tar.gz  -> coder_little-coder-polyglot
    little-coder-sessions-*.tar.gz  -> coder_little-coder-sessions

No file the sidecar produces matches any of those patterns, and the archive it
does produce holds all five volumes under one root, so it could not be restored
into any single volume even if the pattern matched. The backups are real and
fresh; the documented restore path for them does not work.

`scripts/checks/check-backup-coverage.ps1` maps all five volumes to
`little-coder-backup` and passes — coverage checks that a backup EXISTS, never
that it can be restored. This is the same class as the note in
`ai-stack-observability-audit`: a sidecar can exit 0, produce artifacts, and
still leave you with nothing usable.

Two fixes, either works: make the script emit one prefixed tar per volume (the
shape `restore-from-snapshot.ps1` already expects, and what
`ao-worker-*-journals-backup` now does), or add a restore type that extracts
named subdirectories into separate volumes.

**Deliberately not fixed here:** it is the `coder` plane, outside this anchor,
and it wants its own test — a restore drill, not a coverage assertion.

---

## F2 — agent-org is absent from `scripts/lib/stack-services.json`

`grep -c agent-org scripts/lib/stack-services.json` = **0**. The file carries
`coder`, `frontend`, `inference`, `memory`, `open-brain`, `search` — every
plane except agent-org. SERVICE-LIFECYCLE row 8 says this file feeds the
sysadmin MCP's `stack_health` / container tooling, so the entire agent-org
plane (Mattermost, the bridge, both Postgres stores, the workers) is invisible
to that surface.

This is why Phase 0 did NOT add its two new backup sidecars there: adding two
rows for a plane that has none would imply the plane is covered when it is not.

**Deliberately not fixed here:** registering a whole plane is its own item with
its own verification (does `stack_health` actually report agent-org correctly
afterwards?), not a side effect of a memory-plane phase.

---

## F3 — the misleading comment on `openbrain-mcp`'s network posture

`OB1/docker/docker-compose.yml:106-110` says openbrain-mcp is "reachable only
on internal networks. Trusted local clients (Open WebUI via openbrain-mcpo,
recipes on obnet) talk to it directly." Read alone, that says obnet.

The `networks:` block two lines below lists **`obnet` AND `llm-net`**, and
`llm-net` is `ai-stack_llm-net` (external) — which agent-bridge is also on.
That is precisely why Phase 0's `AO_OPENBRAIN_URL` fix works, and the comment
argues against it.

Low severity, but it is the kind of comment that produces a wrong "zero
references" verdict. Worth a one-line amendment when OB1 is next touched.
(OB1 is a submodule: changes push to the OB1 remote FIRST, then a gitlink
bump — not something to fold into an ai-stack-repo phase.)

---

## RUNTIME VALIDATION (2026-08-29, operator granted container access)

The test plan originally said the sidecar run and the live Open Brain smoke were out of
reach. The operator opened container access, so both gaps were closed as far as they can be
closed without deploying unmerged work. **No production container was restarted, recreated
or modified** — recreating `ao-worker-1/2` to add the journals mount would be deploying
unreviewed compose changes to live containers, which is a different act from restarting one
and is deliberately left for after the merge.

**V1 — the data loss this phase prevents is REAL and is happening now.** In the running
`ao-worker-1`:

    /var/lib/little-coder/journals: audit.jsonl (12,933 B, mtime 2026-08-28 23:36),
    errors.jsonl, outcomes.jsonl, tool_calls.jsonl (111,546 B) — 160K total
    ao-worker-2: 244K, same four files

And `docker inspect ao-worker-1` lists mounts for **only** `ao-worker-1-sessions` and
`ao-worker-1-workspace`. There is **no volume at the journals path**, so all of it is on the
container's writable layer. The container was created 2026-08-23T16:59Z — roughly six days
of accrued corpus that any `--force-recreate` or image bump would destroy silently. This
moves 0.3 from "a sensible hardening" to "a live hole with data in it".

**V2 — the backup path works end to end, including the restore.** Using throwaway volumes
(no prod names, all removed afterwards) and the real `backup/generic-tar-backup.sh`:

- a journals-shaped volume produced `ao-worker-1-journals-<ts>.tar.gz` + its sentinel, and
  `sha256sum -c` verified **OK**;
- an EMPTY volume produced `PRECHECK SKIP: /data is empty` — it did not write an empty
  tarball, which is the behaviour that stops a good NAS archive being clobbered;
- the archive extracted into a fresh volume with both files, their content and their mtimes
  intact — a genuine 1:1 restore, which is exactly the property F1 shows little-coder's own
  backups do NOT have.

**V3 — openbrain-mcp is reachable on the lane the fix targets.** A throwaway container on
`ai-stack_llm-net` POSTing a `tools/call` to `http://openbrain-mcp:8000/` with no key got
**HTTP 401**. That confirms, without touching a credential: the name resolves on llm-net,
port 8000 is listening, `/` is served rather than 404, and `x-brain-key` is the gate. The
old default (`openbrain-gateway:8061`) is on obnet and does not resolve from here at all.

Still NOT proven, and it cannot be without enabling the flag with a real key: that an
authenticated capture actually lands a row in `thoughts`. That remains an ops step.

### Correction to this item's own claim, on the record

The commit message lists the wrong PATH as the first of four bugs. That is **overstated**.
`openbrain-mcp` serves `app.all("*")` (`index.ts:2052`), so it routes *any* path into the
MCP transport — `/capture_thought` would have reached it too. The path mattered for the
GATEWAY (which routes `/mcp` specifically); against openbrain-mcp it is non-canonical
rather than fatal.

The genuinely fatal bugs were the **host** (unreachable by construction) and the **body**
(a flat REST payload where a JSON-RPC `tools/call` was required, with `metadata` for
`metadata_extra`). The tests still assert `/` because that is the contract the gateway
itself follows and the shape the next reader should copy — but the strict-server fixture's
404 models the canonical contract, not openbrain-mcp's tolerance, and nobody should read it
as proof that the old path 404'd.

---

## Carried forward from earlier items (still open, still not fixed)

These are not new. They are restated so they stay visible rather than aging
out of a chat log.

- **The follows / auto-wake path has the same message-loss window the durable
  inbox closed.** `poll_follows` advances its cursor before `_dispatch_wake`,
  so a crash between the two loses the wake. `work/dfu-inbox` deliberately
  scoped this out (its anchor names it), and it needs its own anchor: it is a
  different subscription mechanism with its own state, not a second call site
  of the inbox.
- **The harness `-Merged` sha-containment guard has zero drill coverage.** An
  edit inverting that guard would still leave `verify-merge-protocol.ps1`
  reporting 51/51 green. The guard is one of the few mechanical protections in
  the merge protocol, and nothing would tell us if it stopped working.
