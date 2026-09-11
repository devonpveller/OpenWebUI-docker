#!/usr/bin/env bash
# Post a notification from a Claude Code session into Mattermost #claude-sessions,
# AS A THREAD PER SESSION.
#
# Two ways it runs:
#   • Manual:    scripts/notify-mattermost.sh "custom message"     ($1 = the message)
#   • Stop hook: Claude Code invokes it with the hook JSON on STDIN. The script reads the
#     `session_id` from that JSON and — if the allowlist file below is present and non-empty —
#     ONLY pings for registered sessions. That way one Claude Code session's "I'm done" pings
#     don't mix with another session running in the same project (e.g. an orchestration session).
#
# Register a session to ping:  echo "<session-id>" >> scripts/.mm-notify-sessions
# (allowlist absent/empty ⇒ ping for ALL sessions, each labelled by its short id.)
#
# WHY THREADS (operator, 2026-09-10). This used to post flat top-level messages into
# #claude-code. Measured that day: the operator had NEVER opened that channel
# (last_viewed_at showed NEVER when measured at 20:25 that evening), nothing in it
# @-mentioned them (mention_count = 0) so nothing bolded or pushed, and because every
# notification was its own root post there was nowhere coherent to reply — "the messages
# are streamed in arbitrarily which also means i can't reply without the messaging being
# confusing". The sender was never broken: posts were landing, the token was valid, the
# channel returned 200. The presentation was.
#
# THIS SCRIPT had no root_id. The repo did: the agent-bridge threads its own posts
# (agent-org/agent-bridge/app/adapters/mattermost.py), as do the claude-sessions bridge
# and the Mattermost MCP server. An earlier version of this comment said root_id appeared
# "nowhere in the agent-bridge, or any script in the repo", which was a repo-wide claim
# drawn from a grep over two wrong paths. What was true is narrower and is the actual
# point: the IDE notifier was the one sender that did not thread.
#
# So: ONE THREAD PER SESSION, in #claude-sessions where the bridge's own sessions already
# live, posting as bot-claude so an IDE session is indistinguishable from a bridge session.
# The convention there is root-announces / replies-carry-content, and this matches it rather
# than inventing a second scheme. Every message mentions the operator, by their decision —
# an unread channel stays unread, and a mention is what survives that.
#
# The bot token is read from a .env at RUN TIME — never hardcoded or committed.
# Best-effort by design: it never fails its caller (a down Mattermost must not break a turn).
set +e

# Derived from THIS script's location, not hardcoded: the hook can invoke it from any
# cwd, and a test running it out of a git worktree must read that worktree's .env and
# write that worktree's state rather than reaching into the operator's main checkout.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)"
[ -z "$ROOT_DIR" ] && ROOT_DIR="d:/Open WebUI/ai-stack"
ENV_CLAUDE="$ROOT_DIR/.env"                                 # CLAUDE_MM_BOT_TOKEN → bot-claude
ENV_AO="$ROOT_DIR/agent-org/docker/.env"                    # AO_MATTERMOST_BOT_TOKEN → bot-pm
CHANNEL="6z9khgkdd7df9q454be6fimw1h"                        # #claude-sessions
API="http://localhost:8065/api/v4/posts"
ALLOW="$ROOT_DIR/scripts/.mm-notify-sessions"               # one session_id per line (gitignored)
THREADS="$ROOT_DIR/scripts/.mm-session-threads"             # "<session_id> <root_post_id>" (gitignored)
MENTION="${MM_OPERATOR_MENTION:-@profnovice}"
# ONE BUDGET FOR THE WHOLE SCRIPT. This runs as a hook with a timeout - 15s on
# Stop and 20s on Notification in the operator's settings - and threading turned
# one curl call into as many as four. Measured in test, attempt 1: a black-holed
# server cost 16.9s, and 25.2s on the retry path, so the hook was KILLED where
# the single-call version could not exceed its 8s. Every call draws from this,
# and a call with no budget left is skipped rather than started.
MM_DEADLINE_SECS="${MM_DEADLINE_SECS:-10}"
_started=$(date +%s 2>/dev/null || echo 0)
remaining() {
  local _now _left
  _now=$(date +%s 2>/dev/null || echo 0)
  _left=$(( MM_DEADLINE_SECS - (_now - _started) ))
  [ "$_left" -lt 0 ] && _left=0
  printf '%s' "$_left"
}
PROJECT="$(basename "$ROOT_DIR")"

# 1) The session id, in priority order. It decides which thread this joins, so a
#    caller that cannot give us stdin must still have a way to say who it is.
#
#    WHY THREE WAYS (found in test, attempt 1): the Notification hook in
#    .claude/settings.local.json reads the hook JSON ITSELF and then invokes this
#    script with `</dev/null`. Those are the "Claude needs your permission"
#    messages - 28 of the last 40 notifications, and precisely the ones the
#    operator has to answer. With only the stdin path they arrive with no session
#    id and post FLAT, which defeats this whole item for its most important
#    message class. That hook file is operator-local and gitignored, so this
#    script cannot depend on it being changed.
sid="${MM_SESSION_ID:-}"
if [ -z "$sid" ] && [ ! -t 0 ]; then
  hook_json=$(cat 2>/dev/null)
  sid=$(printf '%s' "$hook_json" | python -c 'import json,sys
try:
    print((json.load(sys.stdin).get("session_id") or ""))
except Exception:
    print("")' 2>/dev/null)
fi
# ...and last, recover it from the message itself. The Notification hook formats
# "session <8 hex> - ..." into the text it passes, so the id is right there even
# when stdin is gone. Narrow on purpose: 8 hex characters after the word
# "session", nothing else.
if [ -z "$sid" ] && [ -n "$1" ]; then
  sid=$(printf '%s' "$1" | grep -oiE 'session [0-9a-f]{8}' | head -1 | awk '{print $2}')
fi

# 1b) ONE CANONICAL KEY. The Stop hook gives a 36-char uuid on stdin; the
#     Notification hook gives only the 8 hex characters it printed into the text.
#     Keyed as-is, ONE session opens TWO threads - both announcing the same short
#     id - which breaks the anchor's first criterion. Found in test, attempt 2.
#     The 8-hex prefix is the one form every path can produce, so it is the key.
key=$(printf '%s' "$sid" | tr 'A-Z' 'a-z' | tr -cd '0-9a-f' | cut -c1-8)

# 2) Session allowlist: when it exists and is non-empty, only registered sessions ping.
#    COMPARED ON THE SAME PREFIX, for a reason that nearly shipped as an outage.
#    The gate only fires when a session id is KNOWN. The Notification hook used to
#    supply none, so its messages - the permission requests, the ones the operator
#    must answer - skipped the gate entirely. Recovering the id from the text made
#    them subject to it for the first time, and an allowlist holding a 36-char uuid
#    can never equal an 8-hex id, so every one of them would have been silently
#    dropped. Measured on the operator's real machine in test, attempt 2: IDE
#    traffic would have gone to ZERO.
#
#    Matching on the prefix makes a full-uuid entry and a short-form id agree.
if [ -s "$ALLOW" ] && [ -n "$key" ]; then
  awk -v k="$key" '
    { line = tolower($0); gsub(/[^0-9a-f]/, "", line)
      if (substr(line, 1, 8) == k) { found = 1; exit } }
    END { exit(found ? 0 : 1) }' "$ALLOW" 2>/dev/null || exit 0
fi

short="${key:-${sid:0:8}}"
MSG="${1:-🤖 Claude Code finished a turn in ${PROJECT}${short:+ · session \`$short\`} — your move.}"

# bot-claude first, so IDE sessions and bridge sessions share one identity in one channel.
# Falling back to the agent-org bot rather than going silent: a message from the wrong bot
# is recoverable, a message nobody ever sees is not.
tok=$(grep -m1 '^CLAUDE_MM_BOT_TOKEN=' "$ENV_CLAUDE" 2>/dev/null | cut -d= -f2- | tr -d '\r')
[ -z "$tok" ] && tok=$(grep -m1 '^AO_MATTERMOST_BOT_TOKEN=' "$ENV_AO" 2>/dev/null | cut -d= -f2- | tr -d '\r')
[ -z "$tok" ] && exit 0

# post <message> [root_id] → prints the created post id, or NOTHING on failure.
#
# THE FAILURE TEST IS THE HTTP STATUS, not the presence of an `id`. A Mattermost
# ERROR BODY ALSO HAS AN `id`:
#     {"id":"api.post.create_post.root_id.app_error","status_code":400,...}
# Reading `.id` unconditionally made every failure look like a success, so the
# recovery path below could never fire: posting under a deleted root returned
# that error id, the caller treated it as sent, and the message was SILENTLY
# DISCARDED - permanently, because the stale mapping was never dropped. Worse, a
# failed announce wrote `api.context.permissions.app_error` into the map as the
# session's root, silencing that session for the rest of its life. Found in test,
# attempt 1, against a real deleted root.
post() {
  local _msg="$1" _root="$2" _payload _budget _resp
  _payload=$(CHANNEL="$CHANNEL" MSG="$_msg" ROOT="$_root" python -c \
    'import json,os
d = {"channel_id": os.environ["CHANNEL"], "message": os.environ["MSG"]}
r = os.environ.get("ROOT") or ""
if r:
    d["root_id"] = r
print(json.dumps(d))' 2>/dev/null)
  [ -z "$_payload" ] && return 1
  _budget=$(remaining)
  [ "$_budget" -lt 2 ] && return 1
  _resp=$(curl -s -m "$_budget" -w '\n%{http_code}' \
    -H "Authorization: Bearer $tok" -H "Content-Type: application/json" \
    -X POST "$API" -d "$_payload" 2>/dev/null)
  printf '%s' "$_resp" | python -c \
    'import json,sys
raw = sys.stdin.read().rsplit("\n", 1)
if len(raw) != 2 or raw[1].strip() not in ("200", "201"):
    print("")
else:
    try:
        d = json.loads(raw[0])
        pid = d.get("id") or ""
        print(pid if not d.get("status_code") else "")
    except Exception:
        print("")' 2>/dev/null
}

# 3) Find this session's thread root, if it has one.
root=""
if [ -n "$key" ] && [ -f "$THREADS" ]; then
  # Field-exact, not a substring: `xbeef0011` cannot answer for `beef0011`.
  root=$(awk -v s="$key" '$1 == s {print $2; exit}' "$THREADS" 2>/dev/null)
fi

# 4) No root yet → announce the session and remember the post we can reply under.
#    The announce IS the root, matching what the channel already looks like: a root that
#    says what this thread is, and content underneath it.
# Only a real session gets a thread. A manual one-off invocation with no session id
# posts flat: giving it an announce root would double every ad-hoc message into a
# two-post thread nobody will ever reply to, which is the noise this item exists to cut.
if [ -z "$root" ] && [ -n "$key" ]; then
  announce="🧵 **Claude Code session** \`${short:-unknown}\` · \`${PROJECT}\` — started $(date '+%H:%M')${MENTION:+ · $MENTION}"
  root=$(post "$announce" "")
  if [ -n "$root" ] && [ -n "$key" ]; then
    { printf '%s %s\n' "$key" "$root" >> "$THREADS"; } 2>/dev/null
  fi
fi

# 5) Post the message. Under the root when we have one; as its own post when we do not,
#    because a message in the channel beats no message at all.
out=$(post "${MENTION:+$MENTION }$MSG" "$root")

# 6) A root that no longer exists (post deleted, channel purged) must not wedge the session
#    into silence for the rest of its life. Drop the stale mapping and retry as a new thread,
#    once. Anything still failing after that is Mattermost's problem, not this turn's.
if [ -z "$out" ] && [ -n "$root" ]; then
  if [ -n "$key" ] && [ -f "$THREADS" ]; then
    # awk, not `grep -v && mv`: when the map holds ONLY this session's line grep
    # exits 1, the && short-circuits, the mv never runs and the stale entry
    # survives - so the append below produced a DUPLICATE and the dead root was
    # still found first. Found in test, attempt 1.
    awk -v s="$key" '$1 != s' "$THREADS" > "$THREADS.tmp" 2>/dev/null
    mv "$THREADS.tmp" "$THREADS" 2>/dev/null
  fi
  announce="🧵 **Claude Code session** \`${short:-unknown}\` · \`${PROJECT}\` — resumed $(date '+%H:%M')${MENTION:+ · $MENTION}"
  root=$(post "$announce" "")
  if [ -n "$root" ] && [ -n "$key" ]; then
    { printf '%s %s\n' "$key" "$root" >> "$THREADS"; } 2>/dev/null
  fi
  post "${MENTION:+$MENTION }$MSG" "$root" >/dev/null 2>&1
fi

exit 0
