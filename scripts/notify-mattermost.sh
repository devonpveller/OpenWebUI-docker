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
# (last_viewed_at = NEVER), nothing in it @-mentioned them (mention_count = 0) so nothing
# bolded or pushed, and because every notification was its own root post there was nowhere
# coherent to reply — "the messages are streamed in arbitrarily which also means i can't
# reply without the messaging being confusing". The sender was never broken: posts were
# landing, the token was valid, the channel returned 200. The presentation was.
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
PROJECT="$(basename "$ROOT_DIR")"

# 1) If invoked as a hook, STDIN carries the hook JSON → pull the session id.
sid=""
if [ ! -t 0 ]; then
  hook_json=$(cat 2>/dev/null)
  sid=$(printf '%s' "$hook_json" | python -c 'import json,sys
try:
    print((json.load(sys.stdin).get("session_id") or ""))
except Exception:
    print("")' 2>/dev/null)
fi

# 2) Session allowlist: when it exists and is non-empty, only registered sessions ping.
if [ -s "$ALLOW" ] && [ -n "$sid" ]; then
  grep -qxF "$sid" "$ALLOW" 2>/dev/null || exit 0
fi

short="${sid:0:8}"
MSG="${1:-🤖 Claude Code finished a turn in ${PROJECT}${short:+ · session \`$short\`} — your move.}"

# bot-claude first, so IDE sessions and bridge sessions share one identity in one channel.
# Falling back to the agent-org bot rather than going silent: a message from the wrong bot
# is recoverable, a message nobody ever sees is not.
tok=$(grep -m1 '^CLAUDE_MM_BOT_TOKEN=' "$ENV_CLAUDE" 2>/dev/null | cut -d= -f2- | tr -d '\r')
[ -z "$tok" ] && tok=$(grep -m1 '^AO_MATTERMOST_BOT_TOKEN=' "$ENV_AO" 2>/dev/null | cut -d= -f2- | tr -d '\r')
[ -z "$tok" ] && exit 0

# post <message> [root_id] → prints the created post id, or nothing on failure.
post() {
  local _msg="$1" _root="$2" _payload
  _payload=$(CHANNEL="$CHANNEL" MSG="$_msg" ROOT="$_root" python -c \
    'import json,os
d = {"channel_id": os.environ["CHANNEL"], "message": os.environ["MSG"]}
r = os.environ.get("ROOT") or ""
if r:
    d["root_id"] = r
print(json.dumps(d))' 2>/dev/null)
  [ -z "$_payload" ] && return 1
  curl -s -m 8 -H "Authorization: Bearer $tok" -H "Content-Type: application/json" \
    -X POST "$API" -d "$_payload" 2>/dev/null | python -c \
    'import json,sys
try:
    print(json.load(sys.stdin).get("id") or "")
except Exception:
    print("")' 2>/dev/null
}

# 3) Find this session's thread root, if it has one.
root=""
if [ -n "$sid" ] && [ -f "$THREADS" ]; then
  root=$(grep -m1 -F "$sid " "$THREADS" 2>/dev/null | awk '{print $2}')
fi

# 4) No root yet → announce the session and remember the post we can reply under.
#    The announce IS the root, matching what the channel already looks like: a root that
#    says what this thread is, and content underneath it.
# Only a real session gets a thread. A manual one-off invocation with no session id
# posts flat: giving it an announce root would double every ad-hoc message into a
# two-post thread nobody will ever reply to, which is the noise this item exists to cut.
if [ -z "$root" ] && [ -n "$sid" ]; then
  announce="🧵 **Claude Code session** \`${short:-unknown}\` · \`${PROJECT}\` — started $(date '+%H:%M')${MENTION:+ · $MENTION}"
  root=$(post "$announce" "")
  if [ -n "$root" ] && [ -n "$sid" ]; then
    printf '%s %s\n' "$sid" "$root" >> "$THREADS" 2>/dev/null
  fi
fi

# 5) Post the message. Under the root when we have one; as its own post when we do not,
#    because a message in the channel beats no message at all.
out=$(post "${MENTION:+$MENTION }$MSG" "$root")

# 6) A root that no longer exists (post deleted, channel purged) must not wedge the session
#    into silence for the rest of its life. Drop the stale mapping and retry as a new thread,
#    once. Anything still failing after that is Mattermost's problem, not this turn's.
if [ -z "$out" ] && [ -n "$root" ]; then
  if [ -n "$sid" ] && [ -f "$THREADS" ]; then
    grep -v -F "$sid " "$THREADS" > "$THREADS.tmp" 2>/dev/null && mv "$THREADS.tmp" "$THREADS" 2>/dev/null
  fi
  announce="🧵 **Claude Code session** \`${short:-unknown}\` · \`${PROJECT}\` — resumed $(date '+%H:%M')${MENTION:+ · $MENTION}"
  root=$(post "$announce" "")
  if [ -n "$root" ] && [ -n "$sid" ]; then
    printf '%s %s\n' "$sid" "$root" >> "$THREADS" 2>/dev/null
  fi
  post "${MENTION:+$MENTION }$MSG" "$root" >/dev/null 2>&1
fi

exit 0
