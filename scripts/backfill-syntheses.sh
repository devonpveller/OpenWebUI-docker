#!/usr/bin/env bash
# Backfill clickable sources for the legacy research_synthesis rows that have an
# empty metadata.source_ids (the [Source N] citations can't be linked).
#
# Strategy (SAFE: re-run-first, delete-after-verify):
#   for each old synthesis (empty source_ids):
#     1. back it up (synthesis_backfill_backup)
#     2. re-run the research from its original prompt onto its SAME thread
#     3. wait for the job; confirm a NEW synthesis landed on the thread WITH
#        source_ids > 0 (retry up to RETRIES on a transient empty run)
#     4. only if confirmed good: delete the OLD synthesis + its claims/reusable/
#        ungrounded/leaf (cascades handle thread_sources/claim_sources/etc.)
#     5. mark the backup row replaced (status + new_synthesis_id)
#   A transient/empty run never strips a notebook — the old stays until a good
#   replacement exists. Resumable: rows already status='replaced' are skipped.
set -uo pipefail

LOG=/tmp/backfill-120.log
PSQL() { MSYS_NO_PATHCONV=1 docker exec openbrain-db psql -U postgres -d openbrain -At -F'|' -c "$1"; }
RESEARCH_POST() { # $1=query(b64) $2=thread_id -> prints job_id
  docker exec -e Q="$1" -e T="$2" openbrain-wiki node -e '
    const q=Buffer.from(process.env.Q,"base64").toString("utf8");
    const k=process.env.MCP_ACCESS_KEY||"";
    fetch("http://openbrain-research:8000/research",{method:"POST",headers:{"content-type":"application/json","x-brain-key":k},
      body:JSON.stringify({query:q,thread_id:process.env.T,origin:"manual"})})
      .then(r=>r.json()).then(j=>console.log(j.job_id||"")).catch(()=>console.log(""));'
}
JOB_STATUS() { # $1=job_id -> prints status
  docker exec -e J="$1" openbrain-wiki node -e '
    const k=process.env.MCP_ACCESS_KEY||"";
    fetch("http://openbrain-research:8000/research/jobs/"+process.env.J,{headers:{"x-brain-key":k}})
      .then(r=>r.json()).then(j=>console.log(j.status||"?")).catch(()=>console.log("ERR"));'
}

RETRIES=2
log(){ echo "$(date +%H:%M:%S) $*" | tee -a "$LOG"; }

log "=== backfill START ==="
ITEMS=$(PSQL "
SELECT s.id, replace(encode(convert_to(coalesce(s.research_query,''),'UTF8'),'base64'), chr(10), ''),
       (SELECT thread_id FROM thread_sources WHERE source_id=s.id LIMIT 1)
FROM sources s
WHERE s.content_type='research_synthesis'
  AND jsonb_array_length(coalesce(s.metadata->'source_ids','[]'::jsonb))=0
  AND s.id NOT IN (SELECT id FROM synthesis_backfill_backup WHERE status='replaced')
ORDER BY s.created_at LIMIT ${BACKFILL_LIMIT:-100000};")
TOTAL=$(printf '%s\n' "$ITEMS" | grep -c .)
log "items to process: $TOTAL"
N=0; OK=0; FAIL=0

printf '%s\n' "$ITEMS" | while IFS='|' read -r SID QB64 TID; do
  [ -z "$SID" ] && continue
  N=$((N+1))
  QUERY=$(printf '%s' "$QB64" | tr -d '\r' | base64 -d 2>/dev/null)
  log "[$N/$TOTAL] $SID thread=$TID :: ${QUERY:0:60}"
  if [ -z "$TID" ]; then log "  SKIP: no thread_id"; continue; fi

  # 1. backup (idempotent)
  PSQL "INSERT INTO synthesis_backfill_backup (id,research_query,thread_id,content,metadata,created_at)
        SELECT id, research_query, '$TID', content, metadata, created_at FROM sources WHERE id='$SID'
        ON CONFLICT (id) DO NOTHING;" >/dev/null

  GOOD=""; NEWID=""
  for attempt in $(seq 0 $RETRIES); do
    [ "$attempt" -gt 0 ] && log "  retry $attempt (previous run empty)"
    JID=$(RESEARCH_POST "$QB64" "$TID" | tr -d '\r')
    if [ -z "$JID" ]; then log "  research POST failed"; sleep 10; continue; fi
    # poll up to ~20 min (raised from 12 min 2026-06-14: the llm-queue admission
    # controller serializes a research job's many LLM calls behind ~57s waits, so
    # a single job can take ~11-15 min under contention; the old 12-min cutoff
    # risked abandoning jobs that were about to succeed → wasteful retries).
    for i in $(seq 1 80); do
      ST=$(JOB_STATUS "$JID" | tr -d '\r')
      [ "$ST" = "done" ] && break
      [ "$ST" = "error" ] && break
      sleep 15
    done
    sleep 6  # let the curator finish persisting
    # newest synthesis on this thread WITH source_ids
    RES=$(PSQL "SELECT s.id, jsonb_array_length(coalesce(s.metadata->'source_ids','[]'::jsonb))
                FROM sources s JOIN thread_sources ts ON ts.source_id=s.id
                WHERE ts.thread_id='$TID' AND s.content_type='research_synthesis' AND s.id<>'$SID'
                  AND s.created_at > now()-interval '20 minutes'
                ORDER BY s.created_at DESC LIMIT 1;")
    NEWID=$(printf '%s' "$RES" | cut -d'|' -f1)
    NSRC=$(printf '%s' "$RES" | cut -d'|' -f2)
    log "  job=$ST new=$NEWID source_ids=${NSRC:-0}"
    if [ -n "$NEWID" ] && [ "${NSRC:-0}" -gt 0 ] 2>/dev/null; then GOOD=1; break; fi
  done

  if [ -z "$GOOD" ]; then
    log "  FAIL: no good replacement after retries — leaving old in place"
    PSQL "UPDATE synthesis_backfill_backup SET status='retry_failed' WHERE id='$SID';" >/dev/null
    FAIL=$((FAIL+1)); continue
  fi

  # 4. delete the OLD synthesis (claims/reusable/ungrounded explicit; source cascades the rest)
  PSQL "DELETE FROM reusable_claims WHERE synthesis_id='$SID';
        DELETE FROM ungrounded_claims WHERE synthesis_id='$SID';
        DELETE FROM claims WHERE synthesis_id='$SID';
        DELETE FROM sources WHERE id='$SID';" >/dev/null
  docker exec openbrain-wiki sh -c "rm -f /wiki/content/source/$SID.md" 2>/dev/null
  PSQL "UPDATE synthesis_backfill_backup SET status='replaced', new_synthesis_id='$NEWID' WHERE id='$SID';" >/dev/null
  log "  OK: replaced with $NEWID (old deleted)"
  OK=$((OK+1))
done

log "=== backfill DONE ==="
PSQL "SELECT status, count(*) FROM synthesis_backfill_backup GROUP BY status;" | tee -a "$LOG"
