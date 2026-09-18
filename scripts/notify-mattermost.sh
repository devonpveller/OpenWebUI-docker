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
# VALIDATED, because it is an environment variable and every arithmetic test in
# this file consumes it. A tester set it to "10s" and got 1694 bytes of bash
# arithmetic errors on the hook's stderr; "abc" was worse - bash reads a bare
# identifier as 0, so the budget became zero, every call was skipped for want of
# time and the run sent NOTHING while exiting 0. A knob that can silence the
# notifier by typo is the same failure this item exists to end, reachable from a
# shell profile. Anything that is not a positive integer falls back to the
# default rather than propagating.
case "$MM_DEADLINE_SECS" in
  ''|*[!0-9]*) MM_DEADLINE_SECS=10 ;;
  # ALL DIGITS IS NOT ENOUGH. A tester passed 9223372036854775808 - every
  # character a digit - and bash's arithmetic overflowed, leaking 423 bytes of
  # "integer expression expected" to the hook's stderr from two separate lines.
  # The digit test closed the "10s" case and not this one.
  #
  # The guard that replaced it said "no sane budget has five digits" and was an
  # EIGHT-character test, so 99999 sailed through and produced `curl -m 99998` -
  # a comment describing a stricter rule than the code it sits on, which is the
  # defect this item keeps finding in prose and had not yet made in code. Both
  # ends are bounded now, and the bound is stated as the number it is.
  ??????*) MM_DEADLINE_SECS=10 ;;
esac
[ "$MM_DEADLINE_SECS" -lt 1 ] && MM_DEADLINE_SECS=10
# AND AN UPPER BOUND, for the same reason the lower one exists: the budget becomes
# a curl timeout, so an absurd value is an absurd timeout inside a hook that has
# its own limit. 60 is far above any real setting and far below anything that
# could wedge a turn.
[ "$MM_DEADLINE_SECS" -gt 60 ] && MM_DEADLINE_SECS=60

# A CLOCK THAT DOES NOT FORK. `date +%s` costs a process every time it is read,
# and `remaining` is read before every HTTP call; on Windows a fork is tens of
# milliseconds and this script's whole overhead budget is a fraction of a second.
# A tester measured the locked version 0.6s slower per run UNCONTENDED, where no
# waiting happens at all - that gap is process spawns, not waiting, and at a small
# MM_DEADLINE_SECS it was enough to lose the message entirely. bash 5 exposes
# EPOCHSECONDS as a variable; older shells fall back to the fork.
now_s() {
  if [ -n "${EPOCHSECONDS:-}" ]; then printf '%s' "$EPOCHSECONDS"
  else date +%s 2>/dev/null || echo 0; fi
}
_started=$(now_s)

# TIME SPENT WAITING FOR THE LOCK IS NOT TIME SPENT POSTING, and charging it to
# the same clock is what made this version drop messages the unlocked parent
# delivered. The budget exists to bound HTTP work so the hook returns; local
# waiting is real elapsed time but it is not the server's fault and must not eat
# the send allowance. It is credited back here, capped, so the total stays inside
# the hook's own timeout.
# The floor a first send is guaranteed, matching the fixed `-m 8` of the sender
# this replaces. Worst case is startup + this + the lock wait, which stays inside
# the Stop hook's 15 seconds at the default budget.
POST_FLOOR=8
# THE BUDGET BELOW WHICH THIS DOES NOT THREAD AT ALL - not create a thread, and
# not USE one either. The two are separate decisions and only the first was gated,
# which is the whole of the parity loss an attempt-19 tester measured.
#
# A dead root costs TWO calls: one to learn it is dead, one to send. The pre-item
# notifier `6829474` always costs ONE. At 6s a call the second does not fit inside
# what the hook can afford, so the message is lost where the code being replaced
# delivers it - 14/15 and 13/15 against 15/15 at budgets 8 and 9, measured over 15
# rounds in two independent sweeps. (I published 10/10 there off TEN rounds. A
# ten-round sample has about one chance in three of missing a 10% failure rate,
# and this is the third figure in this item I have published off too few rounds.)
#
# So below this budget the map is not read, no root is used, no recovery can be
# needed, and the run makes exactly the one floored call the parent makes. PARITY
# BY CONSTRUCTION rather than by tuning - threading is what degrades, never
# delivery, which is the one direction this item is allowed to fail in.
MM_THREAD_MIN_BUDGET=10
# THE HOOK'S LIMIT IS NOT THE HTTP BUDGET, AND ADDING UP SEPARATELY-TUNED TERMS
# IS NOT A BOUND. Three terms decided the worst case - startup, the lock wait, and
# the floored first send - each bounded on its own and then SUMMED, each retuned
# in a different round against a different measurement. 8 + 4 + startup fits
# inside 15 the way three estimates fit inside a number: on average.
#
# A tester ran the worst case TWENTY times instead of five: min 12.63, p50 13.36,
# p90 15.08, max 15.77 - THREE OF TWENTY over the Stop hook's 15s. My own evidence
# for this was "12, 12, 13, 14, 14 - deterministic", which is five samples of a
# distribution with a tail, and the commit immediately before this one RETRACTS
# THE SAME MISTAKE made with three samples. Twice is not bad luck; a handful of
# runs cannot see a p90, and I published one as if it could.
#
# So the terms stop being independent. This is a wall-clock ceiling for the WHOLE
# run, and both the wait and the send clamp to what is left of it, rather than
# each holding a private bound that only sums correctly by luck. The sum cannot
# exceed the wall because there is no longer a sum.
#
# 11, not 15: the Stop hook's limit has to cover interpreter startup before
# `_started` is even read, plus curl's own overshoot past `-m`. The measured gap
# between this file's nominal worst case and the wall clock was ~2.4s, so the
# ceiling is set with that much room and the hook's 15 is never the number being
# aimed at. Overridable for the Notification hook's 20s.
MM_WALL_SECS="${MM_WALL_SECS:-11}"
case "$MM_WALL_SECS" in
  ''|*[!0-9]*) MM_WALL_SECS=11 ;;
  ??????*) MM_WALL_SECS=11 ;;
esac
[ "$MM_WALL_SECS" -lt 3 ] && MM_WALL_SECS=3
[ "$MM_WALL_SECS" -gt 60 ] && MM_WALL_SECS=60
# Fork-free, like `remaining` - it is read before every call and inside the wait.
wall_left() {
  local _l=$(( MM_WALL_SECS - ( $(now_s) - _started ) ))
  [ "$_l" -lt 0 ] && _l=0
  printf '%s' "$_l"
}
_lock_spent=0
# 2, not 3. The credit is real time the run spends, so it extends the worst case:
# MM_DEADLINE_SECS + this + startup. A tester measured 14.9-15.8s against the Stop
# hook's 15s limit, three of four runs over. Removing the announce takes a whole
# curl timeout out of that worst case; this takes another second, and both are
# needed because the budget is wall-clock and the hook's limit is not negotiable.
LOCK_SPEND_CAP=2
# `_recovered` lived here and is GONE with the recovery that set it. A variable
# declared and never read is a reader being told a path exists that does not.

# ANNOUNCE_MIN_BUDGET IS GONE, and so is the threshold it named. It gated a
# second API call on 5 seconds of remaining budget, a figure measured against
# STARTUP cost; two calls at 3-5s each need 6-10s, so it protected the message
# only against the one cost it had been fitted to. The thread root is now the
# first message itself - one call - which removes the trade rather than tuning
# where it falls.

remaining() {
  local _now _left
  _now=$(now_s)
  _left=$(( MM_DEADLINE_SECS + _lock_spent - (_now - _started) ))
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
#    messages - the largest class of notification by volume, and precisely the
#    ones the operator has to answer. NO COUNT: the figure that stood here was a
#    reading over a sliding window of recent notifications, and it moved the next
#    time anyone looked (28 of 40 when written, 24 of 40 when a tester re-measured
#    it). A number with no command beside it cannot be re-derived, and a number
#    taken over a window that moves cannot be right for long. The CLASS does not
#    move, and the class is the argument. With only the stdin path they arrive with no session
#    id and post FLAT, which defeats this whole item for its most important
#    message class. That hook file is operator-local and gitignored, so this
#    script cannot depend on it being changed.
sid="${MM_SESSION_ID:-}"
if [ -z "$sid" ] && [ ! -t 0 ]; then
  # Same brace-the-assignment fix as the lock's read below: a `2>/dev/null` INSIDE
  # a command substitution silences the command, but the warning bash itself prints
  # about a NUL byte in the captured output escapes it and reaches the hook's
  # stderr. This one is PRE-EXISTING - the parent leaks here too - and is fixed
  # anyway, because it is one line and the identical defect to the one I added.
  { hook_json=$(cat 2>/dev/null); } 2>/dev/null
  # BASH, NOT PYTHON. Every process this script spawns before its first API call
  # comes out of the SAME wall-clock budget the message has to fit in, and on
  # Windows a fork is tens of milliseconds. A tester measured 3-4 seconds of fixed
  # startup charged to a 10-second budget, which is why runs arrived at the lock
  # too poor to wait and why the budget ran out entirely at smaller settings. This
  # was a python interpreter launched to read ONE field.
  #
  # The field is a uuid in a flat hook payload, so a regex is enough; python stays
  # as the fallback for a shape this does not match, which costs a fork only when
  # the cheap path fails.
  if [[ "$hook_json" =~ \"session_id\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]]; then
    sid="${BASH_REMATCH[1]}"
  else
    sid=$(printf '%s' "$hook_json" | python -c 'import json,sys
try:
    print((json.load(sys.stdin).get("session_id") or ""))
except Exception:
    print("")' 2>/dev/null)
  fi
fi
# ...and last, recover it from the message itself. The Notification hook formats
# "session <8 hex> - ..." into the text it passes, so the id is right there even
# when stdin is gone. Narrow on purpose: 8 hex characters after the word
# "session", nothing else.
if [ -z "$sid" ] && [ -n "$1" ]; then
  # Three more processes for one substring - grep, head and awk - on the path that
  # serves the Notification hook, which is the majority of notifications. Bash can
  # do this without leaving the shell.
  if [[ "$1" =~ [Ss][Ee][Ss][Ss][Ii][Oo][Nn][[:space:]]+([0-9a-fA-F]{8}) ]]; then
    sid="${BASH_REMATCH[1]}"
  fi
fi

# 1b) ONE CANONICAL KEY. The Stop hook gives a 36-char uuid on stdin; the
#     Notification hook gives only the 8 hex characters it printed into the text.
#     Keyed as-is, ONE session opens TWO threads - both announcing the same short
#     id - which breaks the anchor's first criterion. Found in test, attempt 2.
#     The 8-hex prefix is the one form every path can produce, so it is the key.
#     Filtered to ALPHANUMERIC, not hex. Hex-only was a filter, not a prefix:
#     `my-session` and `sess` both reduced to `e` and shared a thread, and an id
#     with no hex characters reduced to EMPTY - which posts flat AND skips the
#     allowlist, since the gate needs a key. Measured in test, attempt 3. For real
#     uuids the two agree exactly, so no hook path changes.
# ONE NORMALISER, used by the key below AND by the allowlist gate. They were
#     two separate expressions and drifted: attempt 3 made the key alphanumeric
#     and left the gate filtering hex, so a session listed VERBATIM was silenced
#     whenever its id was not pure hex. A fix applied to one consumer of a value
#     and not the other is how this item has failed twice now.
#
#     TRUNCATE ONLY WHAT IS LONG ENOUGH TO BE A UUID. `cut -c1-8` on everything
#     collapsed `testsess-mmtest4-aaa` and `-bbb` to the same `testsess` - the
#     alphanumeric fix traded one collision class for another, measured in test
#     attempt 4. A uuid is 32 hex characters once punctuation is stripped, and the
#     Notification hook emits its first 8; anything shorter is used whole, so two
#     similar non-uuid ids stay distinct.
normkey() {
  printf '%s' "$1" | tr 'A-Z' 'a-z' | tr -cd '0-9a-z' | awk '
    { if (length($0) >= 32) print substr($0, 1, 8); else print substr($0, 1, 32) }'
}
key=$(normkey "$sid")

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
# AN EMPTY KEY MUST NOT BYPASS THE GATE. `[ -n "$key" ]` meant a session id that
# normalises to nothing - `-----`, or anything with no alphanumerics - skipped the
# allowlist entirely and posted. Found in test, attempt 5. If a list exists and we
# cannot identify ourselves, we are not on it.
if [ -s "$ALLOW" ] && [ -z "$key" ]; then
  exit 0
fi
if [ -s "$ALLOW" ] && [ -n "$key" ]; then
  _hit=""
  while IFS= read -r _entry || [ -n "$_entry" ]; do
    [ -z "$_entry" ] && continue
    [ "$(normkey "$_entry")" = "$key" ] && { _hit=1; break; }
  done < "$ALLOW"
  [ -z "$_hit" ] && exit 0
fi

short="${key:-${sid:0:8}}"
MSG="${1:-🤖 Claude Code finished a turn in ${PROJECT}${short:+ · session \`$short\`} — your move.}"

# bot-claude, and ONLY bot-claude, so IDE sessions and bridge sessions share one
# identity in one channel.
#
# There WAS a fallback to the agent-org bot here, justified as "a message from the
# wrong bot is recoverable, a message nobody sees is not". That justification was
# false: bot-pm is not a member of #claude-sessions and posting there returns
# HTTP 403 (measured, test attempt 3). The fallback bought nothing but a wasted
# call against this script's wall-clock budget, and a comment telling the next
# reader something untrue about what happens when the token goes missing.
tok=$(grep -m1 '^CLAUDE_MM_BOT_TOKEN=' "$ENV_CLAUDE" 2>/dev/null | cut -d= -f2- | tr -d '\r')
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
  local _msg="$1" _root="$2" _floor="${3:-0}" _payload _budget _resp _wl
  _payload=$(CHANNEL="$CHANNEL" MSG="$_msg" ROOT="$_root" python -c \
    'import json,os
d = {"channel_id": os.environ["CHANNEL"], "message": os.environ["MSG"]}
r = os.environ.get("ROOT") or ""
if r:
    d["root_id"] = r
print(json.dumps(d))' 2>/dev/null)
  [ -z "$_payload" ] && return 1
  # THE MESSAGE GETS A FLOOR, LIKE THE SENDER THIS REPLACES. The parent gives
  # every call a fixed `curl -m 8`; this gave each call whatever was left of a
  # wall-clock budget that its own startup had already spent 3-4 seconds of. So
  # the timeout shrank as overhead accumulated, and at MM_DEADLINE_SECS=5 a tester
  # measured 9 of 18 runs making NO HTTP CALL AT ALL - `post` returned at the
  # "under 2 seconds" test. That is not the lock and not the threading: it is the
  # budget design, and it lost messages the code being replaced delivers.
  #
  # The FIRST send therefore gets at least POST_FLOOR seconds no matter what the
  # budget says, and is never skipped. Later calls - the recovery retry - still
  # draw on what is left, because by then the operator already has the message and
  # a second attempt is worth only the time actually available.
  _budget=$(remaining)
  # THE CALLER DECIDES, because a flag set here cannot reach it. The previous
  # version tracked "is this the first send?" in a variable assigned INSIDE this
  # function - and every reading call site invokes it as `$(post ...)`, a
  # subshell, so the assignment died with it and EVERY call got the floor. That is
  # the third time this exact trap has been hit in this file, twice after it was
  # written up in a comment a few dozen lines away. A tester instrumented it:
  # `curl -m 8` AND `curl -m 8` on a dead-root retry, where the comment promised
  # "later calls still draw on what is left".
  #
  # Writing it as a parameter makes the mistake unavailable: there is nowhere to
  # put a flag that does not survive, because the decision is made where the
  # knowledge is.
  [ "$_floor" -gt 0 ] && [ "$_budget" -lt "$_floor" ] && _budget="$_floor"
  # AND THE FLOOR IS CLAMPED BY THE WALL, because a floor that can raise the
  # timeout ABOVE what the hook can afford is precisely how the worst case got
  # out. The floor's job is to stop a call being starved by accumulated overhead;
  # it was also, silently, licensed to overrun the Stop hook, and at a hung server
  # that is exactly what it did. The message's guarantee is "a real attempt", not
  # "eight seconds regardless of whether anyone is still listening".
  # THE WALL MAY BOUND OPTIONAL WORK. IT MAY NOT BOUND THE MESSAGE.
  # This clamp ran unconditionally, AFTER the floor above, and silently undid it:
  # a run reaching here with the wall spent had its floored budget cut to 0 or 1,
  # was then refused outright by the guard below, and issued NO curl at all --
  # exiting 0 with empty stderr. Two comments in this file assert that cannot
  # happen ("the FIRST send ... is never skipped"; "a call that has been given the
  # floor above is never refused here"). They stated the intent; this line
  # contradicted it.
  #
  # Measured 2026-09-18 at the SHIPPED DEFAULT (MM_WALL_SECS=11): 9s of pre-send
  # overhead -> zero HTTP calls, exit 0, message gone. NO CONCURRENCY NEEDED -- a
  # loaded machine is enough. Under ten-way concurrency it cost 5 of 9 rounds, one
  # of them 0 of 10 delivered, where the pre-item sender delivered 10 of 10.
  # Isolated to this line: at MM_WALL_SECS=60, nothing else changed, 0 of 8 rounds
  # lost anything.
  #
  # The floor now survives the wall; the wall still bounds every OPTIONAL call.
  _wl=$(wall_left)
  [ "$_floor" -le 0 ] && [ "$_budget" -gt "$_wl" ] && _budget="$_wl"
  # OUT OF BUDGET IS NOT A DEAD ROOT. Both used to return an empty string, so the
  # caller could not tell "the server rejected this root" from "there was no time
  # left to ask" - and the recovery path fired on the second, spending the little
  # time that remained on an announce plus a retry, which is the most expensive
  # possible response to being short of time. The flag lets the caller skip it.
  # A call that has been given the floor above is never refused here; only a later
  # one can be. (No flag is set for the caller: `post` is invoked as
  # `$(post ...)` at the sites that read its result, and a command substitution is
  # a subshell, so an assignment would die with it. The caller reads `remaining`
  # itself.)
  # A FLOORED CALL IS NEVER REFUSED. Only an optional later call can be.
  [ "$_floor" -le 0 ] && [ "$_budget" -lt 2 ] && return 1
  _resp=$(curl -s -m "$_budget" -w '\n%{http_code}' \
    -H "Authorization: Bearer $tok" -H "Content-Type: application/json" \
    -X POST "$API" -d "$_payload" 2>/dev/null)
  # A DEAD ROOT AND A BAD MINUTE ARE DIFFERENT ANSWERS, and this used to give the
  # same one for both: empty. So a single transient 500, 503 or 429 on a LIVE root
  # made the caller conclude the root was dead, open a NEW thread and append it to
  # the map - and every later message followed the new root, abandoning the
  # operator's thread mid-conversation. A tester demonstrated it end to end.
  #
  # Mattermost names this failure precisely: the error body for a deleted or
  # invalid root carries `root_id` in its id. That is the only response that
  # justifies re-rooting, so it is the only one that says so, by printing a
  # sentinel the caller can test. Everything else stays empty and means "not this
  # time" rather than "never again".
  printf '%s' "$_resp" | python -c \
    'import json,sys
raw = sys.stdin.read().rsplit("\n", 1)
if len(raw) != 2 or raw[1].strip() not in ("200", "201"):
    body = raw[0] if raw else ""
    try:
        eid = json.loads(body).get("id") or ""
    except Exception:
        eid = ""
    print("!deadroot" if "root_id" in eid else "")
else:
    try:
        d = json.loads(raw[0])
        pid = d.get("id") or ""
        print(pid if not d.get("status_code") else "")
    except Exception:
        print("")' 2>/dev/null
}

# ---------------------------------------------------------------------------
# THE ANNOUNCE LOCK.
#
# Steps 3 and 4 are a read-then-act: look for this session's root, and if there
# is none, create it. Two first notifications from the SAME session can both read
# "no root" and both announce. Measured in test attempt 8: three concurrent runs
# produced THREE roots and three map lines - which defeats the one-thread-per-
# session this item exists to deliver. It is reachable in ordinary use because the
# Notification hook is asynchronous, so a permission request and a turn completion
# from one session overlap.
#
# MAKING THE MAP APPEND-ONLY COULD NEVER HAVE FIXED THIS. That removed the race
# over the FILE; this is a race over an ACTION. The earlier fix measured clean and
# was not wrong - it was answering a different question, which is why this survived
# it. Two conflicts that look alike because they involve the same file are not the
# same conflict.
#
# mkdir, because then the test-and-set IS the syscall rather than two lines of
# shell. PER SESSION, because two different sessions announcing at the same moment
# are not in conflict and must not queue behind each other inside a 10s budget.
#
# EVERY failure path below falls through to announcing WITHOUT the lock - exactly
# the behaviour it replaces. So the lock can make the common case correct and
# cannot make any case worse, and nothing in it may break a turn (T7).
LOCK=""
lock_release() {
  [ -n "$LOCK" ] && rm -rf "$LOCK" 2>/dev/null
  LOCK=""
}
trap 'lock_release' EXIT INT TERM

# HOW LONG TO WAIT, and it is NOT the posting budget. The previous version tied
# the two together and they pull in opposite directions: raising the reserve so a
# contended lock could not eat the send budget ALSO meant a run that started
# slowly gave up without waiting at all - a tester measured 5 to 7 of 10 runs
# abandoning the lock instantly, and ten concurrent runs opening up to ten roots.
# Fixing the silence broke the waiting, because one number was doing both jobs.
#
# So: the WAIT is bounded by its own try count, and the budget is only a backstop
# that stops us waiting past being able to send at all.
LOCK_MAX_TRIES=$(( (MM_DEADLINE_SECS * 3) / 2 ))
# 8 turns, about 1.6s. It was 15 (~3s), and at ten concurrent runs that wait
# cost 10 messages in 100 where the unlocked code lost none - the losers spent
# their budget waiting for a root instead of sending. Measured at 15 / 8 / 5
# turns: 10, 1 and 8 messages lost per 100 at N=10, with N=2 and N=3 perfect
# throughout. 5 is too short to see the winner publish and falls back to flat
# more often; 8 is where waiting still usually works and no longer costs the
# message. A loser that times out posts flat, which delivers.
# 10 turns, about 3.6 SECONDS AT THE MEASURED COST of a turn - sized to cover one
# API round-trip, which is what the wait is FOR.
#
# IT WAS BRIEFLY 6, AND 6 BROKE THE THING THE WAIT EXISTS FOR: with a 2s API,
# 9 of 15 rounds at N=2 split, because the wait expired before the winner could
# publish. That is the same mistake three earlier attempts made - shortening the
# wait to buy wall time, and paying for it in the guarantee.
#
# What made 10 affordable is the floor fix beside it. While the POST_FLOOR flag
# was dying in a subshell, EVERY call was floored at 8s, so a dead-root retry cost
# a second 8 seconds and the worst case ran 13-16.5s against a Stop hook's 15.
# With the floor applied only to the first send, the same worst case loses a whole
# call. The wall-time problem was never the wait.
[ "$LOCK_MAX_TRIES" -gt 10 ] && LOCK_MAX_TRIES=10
# THE WAIT'S REAL BOUND, in seconds. Three terms have to fit inside the Stop
# hook's 15s: startup (~1.4s), this, and the floored first send (8s, plus the
# time a hung server takes to reach that timeout). Three leaves room for the
# measured tail; four did not.
LOCK_WAIT_SECS=4
# THE COST OF A TURN, MEASURED, because every figure derived from it was wrong
# while it was assumed. A turn is `mkdir` + `cat` + `sleep 0.2`, and the two forks
# are not free on this platform: ten turns take 3.56s, not the 2s that "0.2s per
# turn" implies. A tester measured 4.0-4.3s independently. Every claim about the
# wait, and the entry gate below, were computed from the assumption.
LOCK_TURN_MS=356
[ "$LOCK_MAX_TRIES" -lt 2 ] && LOCK_MAX_TRIES=2
# THREE turns, about 0.6s. The holder writes `at` in the microseconds after its
# mkdir, so this is already a margin of roughly a thousand times the window it has
# to distinguish; the reason it is not larger is that a bigger number needs slack a
# small budget does not have, and then the expiry never fires at all. Measured: at 5
# it left a blind lock behind at MM_DEADLINE_SECS=5, at 3 it does not.
LOCK_BLIND_TRIES=3
# ...but never at or beyond the loop bound, or the blind expiry is unreachable and
# an `at`-less lock survives the run forever - which silently disables threading for
# that session, the failure mode this whole item exists to remove. At the default
# budget the two are 15 and 5 and this clamp does nothing; at MM_DEADLINE_SECS=3 the
# bound is 4, and without the clamp a blind lock was measured surviving every run.
# Two constants that must stay ordered cannot be left to whoever edits one of them.
[ "$LOCK_BLIND_TRIES" -ge "$LOCK_MAX_TRIES" ] && LOCK_BLIND_TRIES=$(( LOCK_MAX_TRIES - 1 ))
[ "$LOCK_BLIND_TRIES" -lt 1 ] && LOCK_BLIND_TRIES=1

# Accounts for its own elapsed time, so `remaining` can give it back. Every exit
# path goes through _lock_done, because a credit that only some returns apply is
# worse than none - it would make the budget depend on which branch was taken.
lock_done() {
  local _spent=$(( $(now_s) - _lock_t0 ))
  [ "$_spent" -lt 0 ] && _spent=0
  [ "$_spent" -gt "$LOCK_SPEND_CAP" ] && _spent="$LOCK_SPEND_CAP"
  _lock_spent=$(( _lock_spent + _spent ))
  return "$1"
}

# THE MAP LOOKUP, WITHOUT A FORK. This was `$(awk ...)` - two processes per call,
# one for the substitution and one for awk - and the lock's poll loop runs it on
# every turn, so a losing run could spend a dozen process spawns just asking
# whether the root had appeared yet. At ten concurrent runs that is what pushed
# some of them past their budget with no message sent, while the unlocked code
# they replace sent everything. It writes a global instead of printing, because a
# command substitution would put the fork straight back.
#
# LAST match wins, as before: the map is append-only and a recovery appends a new
# root, so the newest line is the live one.
MAP_ROOT=""
map_root() {
  MAP_ROOT=""
  # A `-` value is the DEAD-ROOT SENTINEL - see where it is read below.
  [ -n "$key" ] || return 0
  # GUARD THE FILE, do not rely on the redirect's `2>/dev/null`. Redirections are
  # applied left to right, so `done < "$THREADS" 2>/dev/null` reports a missing
  # file on the stderr that is still open - 540 bytes of it across a run, in a
  # script whose own test plan asserts zero. The map legitimately does not exist
  # before the first session is recorded, so this is the common path, not an edge.
  [ -r "$THREADS" ] || return 0
  local _k _v
  while read -r _k _v || [ -n "$_k" ]; do
    if [ "$_k" = "$key" ]; then
      if [ "$_v" = "-" ]; then
        # THE DEAD-ROOT SENTINEL: "this id is gone, do not send to it again."
        #
        # It does NOT mean "never thread this session". Reporting no root lets the
        # ORDINARY path create a fresh one on the next run - with a whole budget in
        # hand, no deadline pressure, and no second call in the same run. That is
        # the difference from the recovery this replaced: re-rooting is fine, and
        # re-rooting INSIDE the run that just spent a call discovering the problem
        # is what could not fit in a 15s hook.
        #
        # Cost of a deleted root, measured: ONE extra call, ONCE. Six messages in a
        # session whose root had been deleted took SEVEN calls and delivered 6/6,
        # with threading resumed from the second message.
        #
        # Measured before this was wired: later runs read `-` as a post id and
        # posted with `root_id=-`, so five of six messages went to a thread that
        # does not exist. A sentinel the reader does not know about is worse than
        # no sentinel at all.
        MAP_ROOT=""
      else
        MAP_ROOT="$_v"
      fi
    fi
  done < "$THREADS" 2>/dev/null
  return 0
}

# $1, when given, is a root this caller has ALREADY PROVEN DEAD. The map check
# below must not hand it back.
lock_take() {
  [ -z "$key" ] && return 1
  local _known_dead="${1:-}" _avail _wait
  # ARRIVING ALREADY STARVED? DO NOT THREAD AT ALL.
  #
  # Threading is worth a little time and no messages. A run whose startup has
  # already eaten half the budget - which is what ten concurrent runs do to each
  # other on this machine - cannot afford to take a lock, wait for a root and
  # still send; it was measured losing the message instead, where the unlocked
  # code it replaces lost none. Such a run now skips the whole mechanism and posts
  # flat immediately, which is exactly what the parent would have done.
  #
  # This is the one decision the rest of the file keeps re-learning: when the
  # budget is short, spend it on the message.
  # THE GATE ASKS WHETHER THE WAIT AND THE SEND BOTH FIT, not whether half the
  # budget survives. `MM_DEADLINE_SECS / 2` was arbitrary and far too strict: a
  # tester measured losing runs arriving here with 3 or 4 seconds of a 10-second
  # budget - startup alone spends 3-4 - so the gate fired on EVERY loser, none of
  # them ever entered the wait loop, and they all posted flat against a map the
  # winner had not written yet. The wait had been decoupled from the budget; the
  # ENTRY had not. That is why N=3 split in 5 of 30 rounds while N=2 held.
  #
  # What actually has to fit is the wait (LOCK_MAX_TRIES turns of 0.2s) plus one
  # post (`post` refuses under 2s). Anything less and threading is genuinely
  # unaffordable, which is the only case this gate is for.
  # Derived from the measured turn cost, not from the old 0.2s assumption: the
  # wait can take LOCK_MAX_TRIES turns, and a post needs 2 seconds after it.
  # From the wait's own bound plus one post, rather than from a per-turn constant
  # that was measured idle and wrong under load.
  # THIS REFUSED WHERE IT SHOULD HAVE SHORTENED, and refusing meant no thread at
  # all. The gate compared what was left against the wait's STATIC MAXIMUM plus a
  # post - `LOCK_WAIT_SECS + 2` = 6 - while the wait itself has been bounded by a
  # DEADLINE since the round that stopped trusting a per-turn constant. So once a
  # call cost more than about 2.6s there was never 6 seconds left, `lock_take`
  # returned without the lock, and the `-z "$LOCK"` branch posted FLAT and
  # appended nothing to the map: a dead root re-threaded 5 of 12 times at a 3s
  # API and 0 of 3 at 3.5-3.8s, burning a call on the dead root every time.
  #
  # Not a regression - attempt 16's arithmetic is identical. It was invisible
  # because T6a had only ever been run against an instant-reject shim, where no
  # call is slow enough to close the gate. A case that only ever meets the fast
  # path cannot see a budget bug.
  #
  # A BOUND THAT IS ALREADY DYNAMIC MUST NOT BE GATED ON ITS STATIC MAXIMUM. Take
  # what the wall leaves, keep a post's worth back, and wait for that long. The
  # wait degrades smoothly to nothing instead of falling off a cliff at 6.
  _avail=$(wall_left)
  _wait=$(( _avail - 2 ))
  [ "$_wait" -gt "$LOCK_WAIT_SECS" ] && _wait="$LOCK_WAIT_SECS"
  [ "$_wait" -lt 1 ] && return 1
  # AND A FLOOR ON THE WHOLE BUDGET, not just on what is left of it. Threading
  # costs a lock directory and a map line - two syscalls that are not free on this
  # platform - and below a certain budget those cost the message instead. Measured
  # at 5s per call: MM_DEADLINE_SECS=5 delivered 4 of 6 where the pre-item sender
  # delivered 6, while 8 and 10 were at full parity. So below
  # MM_THREAD_MIN_BUDGET this does not
  # thread AT ALL and the run behaves exactly like the sender it replaces - which
  # is better than threading badly, and honest in a way that tuning the gate one
  # more notch would not have been.
  # THIS COMMENT SAID "under 8" WHILE THE CONSTANT SAYS 10, and an attempt-21
  # tester measured that budgets 8 and 9 do not thread either. A prose figure
  # beside the constant it describes is the defect this very file names a few
  # dozen lines up - a count that a command beside it derives should not also be
  # typed out. The constant is the number; the comment no longer repeats it.
  [ "$MM_DEADLINE_SECS" -lt "$MM_THREAD_MIN_BUDGET" ] && return 1
  local _d="$THREADS.lock.$key" _at _age _i=0
  _lock_t0=$(now_s)
  # BOUNDED BY TIME, NOT BY TURNS. The turn count was sized from a measured cost
  # of 356ms - measured on an IDLE machine, with no lock actually contended. A
  # tester traced real runs and found 550-650ms a turn under the contention the
  # lock exists for, so ten turns took 6.1s where this file claimed 3.8s and the
  # worst case ran past the Stop hook's 15 seconds.
  #
  # A per-turn constant is the wrong thing to measure, because the cost of a turn
  # is exactly what varies under load. A deadline does not care: whatever a turn
  # costs, the wait ends when the clock says so. The turn cap stays as a second
  # bound so the loop terminates even if the clock misbehaves.
  local _wait_until=$(( $(now_s) + _wait ))
  # THE TRY COUNT IS THE LOOP BOUND, unconditionally, and that is the whole point
  # of writing it this way. The previous version bounded itself with `rm -rf; continue`,
  # which skipped BOTH the budget check and the sleep - so when mkdir failed for any
  # reason the rm could not clear, it span forever, forking `date` and `cat` every
  # turn. Measured: it did not return in TEN MINUTES at MM_DEADLINE_SECS=20. A Stop
  # hook that never returns is worse than the silence this item exists to end, and a
  # loop whose termination depends on the filesystem behaving is not bounded at all.
  # Nothing below uses `continue`; every path falls through to the sleep.
  while [ "$_i" -lt "$LOCK_MAX_TRIES" ] && [ "$(now_s)" -lt "$_wait_until" ]; do
    _i=$(( _i + 1 ))
    # THE MAP FIRST, BECAUSE READING IT IS FREE AND `mkdir` IS A PROCESS. A run
    # waiting here wants the ROOT, not the lock, and once the winner has published
    # one there is nothing left to compete for - so checking costs nothing and
    # saves a syscall per turn. With the lock attempted first, every losing run
    # spent up to eight spawns discovering it had already lost.
    # THE MAP CHECK MUST SKIP A ROOT THE CALLER KNOWS IS DEAD, or the recovery
    # path can never re-thread. Without this the dead-root case was unfixable by
    # construction: the stale line is still in the map, nothing removes it (the
    # map is append-only by design), so this short-circuit fired every time, the
    # lock was never taken, and the branches that append a NEW root were
    # unreachable. Measured by an attempt-15 tester: three consecutive runs each
    # burned a call on the dead root, posted flat, and left the map unchanged
    # forever - a session wedged out of its own thread for good.
    #
    # This is the optimisation that fixed concurrency ("wait for the root, not the
    # lock") breaking the case beside it. A short-circuit is a claim that the thing
    # you found is the thing you wanted, and here it was not.
    map_root
    if [ -n "$MAP_ROOT" ] && [ "$MAP_ROOT" != "$_known_dead" ]; then
      root="$MAP_ROOT"; lock_done 1; return 1
    fi
    if mkdir "$_d" 2>/dev/null; then
      # The brace wraps the ASSIGNMENT, not just `cat`. A `2>/dev/null` inside a
      # command substitution silences the command; the warning bash itself prints
      # about a NUL byte in the captured output escapes it, and that leak reaches
      # the hook's stderr.
      { printf '%s' "$(now_s)" > "$_d/at"; } 2>/dev/null
      LOCK="$_d"
      lock_done 0
      return 0
    fi
    # WE WANT THE ROOT, NOT THE LOCK. The moment the holder publishes its map
    # line we have everything we came for, so stop waiting - every turn spent
    # here is taken from the budget that has to carry the actual message, and
    # waiting for the holder to RELEASE rather than to PUBLISH is waiting for
    # something we do not need. Measured over 20 rounds of 10 concurrent runs:
    # waiting for the release lost 2 messages in 200 where the unlocked parent
    # lost none, and that is the wrong way to buy one thread per session.

    { _at=$(cat "$_d/at" 2>/dev/null); } 2>/dev/null
    case "$_at" in
      ''|*[!0-9]*) _age=-1 ;;
      *) _age=$(( $(now_s) - _at )) ;;
    esac
    # Expire a lock whose holder died, two ways, because it can die two ways:
    #   readable and older than the whole budget -> cannot be a live run;
    #   unreadable after LOCK_BLIND_TRIES turns  -> the holder died between its
    #     mkdir and its write, a window microseconds wide for anything alive.
    # Both merely REMOVE the directory and fall through; the next turn's mkdir is
    # what acquires it. One extra sleep is cheaper than a special exit path.
    if [ "$_age" -ge "$MM_DEADLINE_SECS" ] || { [ "$_age" -lt 0 ] && [ "$_i" -ge "$LOCK_BLIND_TRIES" ]; }; then
      rm -rf "$_d" 2>/dev/null
    fi
    # THE BACKSTOP IS GONE. It ended the wait at `remaining <= min(budget/2,3)`,
    # which with 3-4 seconds of startup already spent meant every loser left after
    # two to four turns - about 0.6s, shorter than a single API round-trip, so the
    # winner had not written the map line yet and the thread split anyway. A
    # tester traced 36 rounds and found zero exits at the entry gate and all of
    # them here: the early exit had moved, not gone.
    #
    # It existed to stop waiting from eating the send. The send now has a floor it
    # is always given, so waiting cannot take the message any more, and the try
    # count is the only bound needed.
    sleep 0.2 2>/dev/null || sleep 1
  done
  lock_done 1
  return 1
}

# The lookup MUST happen inside the lock: a run that loses the race re-reads the
# map here and finds the winner's root, instead of announcing a second one.
lock_take || :

# 3) Find this session's thread root, if it has one.
#
# ...BUT NOT WHEN THE BUDGET CANNOT AFFORD THE RECOVERY A DEAD ONE WOULD NEED.
# Reading the map here is what commits the run to a possible second call. Below
# MM_THREAD_MIN_BUDGET it does not read it, so this run is indistinguishable from
# the pre-item sender: one floored call, no root, no recovery, no map write.
root=""
if [ "$MM_DEADLINE_SECS" -ge "$MM_THREAD_MIN_BUDGET" ] && [ -n "$key" ] && [ -f "$THREADS" ]; then
  # Field-exact, not a substring: `xbeef0011` cannot answer for `beef0011`.
  # LAST match wins, not first. The map is append-only, so a recovery appends a
  # new root and the newest line is the live one. That removes the read-modify-
  # write entirely - and with it the race that measurably destroyed an
  # uninvolved session's mapping in 2 of 5 concurrent rounds, and that renaming
  # the temp file did NOT fix (still 2 of 3 at the next attempt). A rename is not
  # a synchronisation primitive; not rewriting the file is.
  map_root; root="$MAP_ROOT"
fi

# 4) No root yet → announce the session and remember the post we can reply under.
#    The announce IS the root, matching what the channel already looks like: a root that
#    says what this thread is, and content underneath it.
# Only a real session gets a thread. A manual one-off invocation with no session id
# posts flat: giving it an announce root would double every ad-hoc message into a
# two-post thread nobody will ever reply to, which is the noise this item exists to cut.
# ONLY THE LOCK HOLDER MAY CREATE A ROOT. `-n "$LOCK"` is the whole invariant, and
# without it the lock leaked its cost back into the failure it prevents: a run that
# waited and then gave up went on to announce anyway, so under a slow API three
# concurrent runs still opened three roots AND lost three messages - the announce is
# a second API call, and the budget spent waiting is gone from the one that carries
# the actual message. Measured at 5s per call: 2 of 3 rounds split the thread and 3
# messages were dropped; the unlocked parent dropped none, which made the lock worse
# than nothing exactly where it mattered.
#
# THERE IS NO SEPARATE ANNOUNCE ANY MORE. THE FIRST MESSAGE IS THE ROOT.
#
# A header post costs a SECOND API call, and every version of this that kept one
# lost messages to it. The last attempt gated it on a 5-second budget, which was
# measured against STARTUP cost and cannot cover two calls that take 3-5s each: a
# tester found the tip announcing and then failing to send at the DEFAULT budget
# once a call cost more than about 2s - 0 of 8 messages delivered at 5s per call,
# every round consisting of the header alone. A thread title announcing a session
# that then says nothing is verbatim the failure this item exists to end, and
# three rounds of tuning a threshold kept reproducing it at a new latency.
#
# So the thread's root IS the session's first message. One call, the same as the
# unlocked code this replaces, and the session still gets exactly one thread: the
# id that call returns is what goes in the map, and every later notification
# replies under it. Nothing can now be lost to a header, because there is no
# header - and a reader opening the thread sees content immediately instead of a
# title. The cost is that the root says "finished a turn" rather than "session
# started", which is what the channel's own convention already looks like.
#
# `-n "$LOCK"` still gates ROOT CREATION, not sending: a run that could not take
# the lock posts flat rather than opening a competing thread, and flat-but-
# delivered is the degradation this item is allowed to make.
_make_root=""
if [ -z "$root" ] && [ -n "$key" ] && [ -n "$LOCK" ]; then
  _make_root=1
fi
# RELEASED HERE ONLY IF THIS RUN IS NOT CREATING THE THREAD.
#
# When the root was a separate announce, the map line was written inside the lock
# and the message could safely go out after releasing. Now that the FIRST MESSAGE
# is the root, the map line cannot exist until that post returns - so releasing
# first left the critical section covering nothing: a second run took the freed
# lock, read a map the winner had not written yet, and opened its own root.
# Measured the moment the change was made: 12 of 12 rounds at N=2 opened two
# roots, indistinguishable from the unlocked code.
#
# So the run creating the thread holds the lock across its post. That is the one
# case where holding it is the point rather than a cost, and it lasts at most one
# API call. Every other run releases here and posts unserialised.
[ -z "$_make_root" ] && lock_release

# 5) THE ONE SEND. Under the session's root when there is one; as its own post
#    otherwise - and when this run is the one creating the thread, the id this
#    call returns becomes that root.
_dead_root="$root"
out=$(post "${MENTION:+$MENTION }$MSG" "$root" "$POST_FLOOR")
if [ -n "$out" ] && [ "$out" != '!deadroot' ] && [ -n "$_make_root" ] && [ -n "$key" ]; then
  { printf '%s %s\n' "$key" "$out" >> "$THREADS"; } 2>/dev/null
fi

# The thread now exists, or the post failed and there is nothing to publish.
# Either way the next run may proceed.
[ -n "$_make_root" ] && lock_release
# 6) A ROOT THAT NO LONGER EXISTS: POST FLAT, AND STOP THREADING THIS SESSION.
#
# THE RECOVERY IS GONE - it re-rooted, and re-rooting is where every measured loss
# in this item lived. It cost a SECOND API call, and a threaded sender needing two
# round-trips where the notifier it replaces needs one cannot match it inside a 15s
# hook: 11-15 of 15 against the parent's 15/15 at the default budget with a 6s API,
# plus the wedged-session bug, the `!deadroot`-into-the-map bug, and the lock
# interaction around the retry. Four rounds of defects for one behaviour.
#
# AND THE BEHAVIOUR WAS NOT WORTH IT. Threading already exists for sessions driven
# from Mattermost - the bridge posts under a root and always has. This script only
# groups the HOOK pings from an IDE session, so the whole feature is tidier
# grouping, and trading delivery for tidier grouping is backwards in a change whose
# entire origin was two months of missed messages.
#
# So: send the message flat, once, and write a sentinel so LATER runs of this
# session do not pay the discovery again. One local append, no API call. The
# session stops being threaded; it never stops being delivered.
# A ROOTED SEND THAT DID NOT CONFIRM IS A ROOT WE CANNOT KEEP USING - whatever the
# reason. This tested `!deadroot` alone, and an attempt-20 tester showed that is the
# WRONG KEY: when the API is slower than the send budget the 400 does not arrive
# inside the call's own timeout, so `post` returns "" instead of the sentinel, and
# NEITHER the flat retry NOR the map write happened. The map kept the dead root and
# every later message repeated it - at a 9s API, 15 of 15 messages lost and 10 of 15
# maps never repaired, against a parent that loses none.
#
# `!deadroot` says "the server rejected this root". An empty result says "this
# message did not land, and I do not know why". Both mean the same thing about the
# ROOT: do not keep sending to it. Keying the response on the reason made the
# recovery depend on being told, and a slow server does not tell you.
#
# Writing the sentinel on a merely TRANSIENT failure costs an extra thread - the
# next run opens a fresh root - and never costs a message. That is the degradation
# this item is allowed to make, and it is the one the whole design already chooses
# everywhere else.
if [ -z "$out" ] || { [ "$out" = '!deadroot' ] && [ -n "$root" ]; }; then
  # RETRY ONLY ON AN EXPLICIT REJECTION, AND WRITE THE SENTINEL ON EITHER.
  #
  # `!deadroot` is the server saying it did NOT create the post, so a flat retry
  # cannot duplicate. An EMPTY result means the call did not confirm - and a call
  # that did not confirm may still have been ACCEPTED, with curl giving up on the
  # response. Retrying that is how one message becomes two, and T20 requires
  # exactly one.
  #
  # Measured at a 9s API against an 8s budget: no duplicate appeared, because by
  # then there is no wall left to retry with either. That is luck, not a design -
  # at a latency just above the budget the time WOULD be there, and so would the
  # second copy. The sentinel costs an extra thread; the retry would cost a
  # duplicate message. Only one of those is the direction this item may fail in.
  [ "$out" = '!deadroot' ] && post "${MENTION:+$MENTION }$MSG" "" "$POST_FLOOR" >/dev/null 2>&1
  # The LAST line wins, so this supersedes the dead mapping without rewriting the
  # file - the same append-only rule that removed the read-modify-write race. The
  # next run reads "no root" and opens a fresh thread through the ordinary path,
  # on a full budget.
  [ -n "$root" ] && [ -n "$key" ] && { printf '%s -\n' "$key" >> "$THREADS"; } 2>/dev/null
fi

exit 0
