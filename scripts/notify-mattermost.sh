#!/usr/bin/env bash
# Post a notification to the Mattermost #claude-code channel.
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
# The bot token is read from agent-org/docker/.env at RUN TIME — never hardcoded or committed.
# Best-effort by design: it never fails its caller (a down Mattermost must not break a turn).
set +e

ENV_FILE="d:/Open WebUI/ai-stack/agent-org/docker/.env"
CHANNEL="qqq97fwxd3f8ufenjybrf5w1yr"                       # #claude-code
API="http://localhost:8065/api/v4/posts"
ALLOW="d:/Open WebUI/ai-stack/scripts/.mm-notify-sessions"  # one session_id per line (gitignored)

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
MSG="${1:-🤖 Claude Code finished a turn in ai-stack${short:+ · session \`$short\`} — your move.}"

tok=$(grep -m1 '^AO_MATTERMOST_BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r')
[ -z "$tok" ] && exit 0

# JSON-encode the message so any characters (emoji, quotes, newlines) are safe.
payload=$(CHANNEL="$CHANNEL" MSG="$MSG" python -c \
  'import json,os;print(json.dumps({"channel_id":os.environ["CHANNEL"],"message":os.environ["MSG"]}))' 2>/dev/null)
[ -z "$payload" ] && exit 0

curl -s -m 8 -H "Authorization: Bearer $tok" -H "Content-Type: application/json" \
  -X POST "$API" -d "$payload" >/dev/null 2>&1
exit 0
