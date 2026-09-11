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
esac
[ "$MM_DEADLINE_SECS" -lt 1 ] && MM_DEADLINE_SECS=10

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
_lock_spent=0
# 2, not 3. The credit is real time the run spends, so it extends the worst case:
# MM_DEADLINE_SECS + this + startup. A tester measured 14.9-15.8s against the Stop
# hook's 15s limit, three of four runs over. Removing the announce takes a whole
# curl timeout out of that worst case; this takes another second, and both are
# needed because the budget is wall-clock and the hook's limit is not negotiable.
LOCK_SPEND_CAP=2
_recovered=""         # set when the recovery path has already sent the message

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
  # OUT OF BUDGET IS NOT A DEAD ROOT. Both used to return an empty string, so the
  # caller could not tell "the server rejected this root" from "there was no time
  # left to ask" - and the recovery path fired on the second, spending the little
  # time that remained on an announce plus a retry, which is the most expensive
  # possible response to being short of time. The flag lets the caller skip it.
  # No flag here. An earlier version set one for the caller to read, but `post` is
  # always invoked as `$(post ...)` and a command substitution is a subshell, so
  # the assignment died with it - dead code from the moment it was written. The
  # caller reads `remaining` itself.
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
[ "$LOCK_MAX_TRIES" -gt 8 ] && LOCK_MAX_TRIES=8
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
  [ -n "$key" ] || return 0
  # GUARD THE FILE, do not rely on the redirect's `2>/dev/null`. Redirections are
  # applied left to right, so `done < "$THREADS" 2>/dev/null` reports a missing
  # file on the stderr that is still open - 540 bytes of it across a run, in a
  # script whose own test plan asserts zero. The map legitimately does not exist
  # before the first session is recorded, so this is the common path, not an edge.
  [ -r "$THREADS" ] || return 0
  local _k _v
  while read -r _k _v || [ -n "$_k" ]; do
    [ "$_k" = "$key" ] && MAP_ROOT="$_v"
  done < "$THREADS" 2>/dev/null
  return 0
}

lock_take() {
  [ -z "$key" ] && return 1
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
  _gate=$(( (LOCK_MAX_TRIES / 5) + 2 ))
  [ "$(remaining)" -lt "$_gate" ] && return 1
  # AND A FLOOR ON THE WHOLE BUDGET, not just on what is left of it. Threading
  # costs a lock directory and a map line - two syscalls that are not free on this
  # platform - and below a certain budget those cost the message instead. Measured
  # at 5s per call: MM_DEADLINE_SECS=5 delivered 4 of 6 where the pre-item sender
  # delivered 6, while 8 and 10 were at full parity. So under 8 this does not
  # thread AT ALL and the run behaves exactly like the sender it replaces - which
  # is better than threading badly, and honest in a way that tuning the gate one
  # more notch would not have been.
  [ "$MM_DEADLINE_SECS" -lt 8 ] && return 1
  local _d="$THREADS.lock.$key" _at _age _i=0
  _lock_t0=$(now_s)
  # THE TRY COUNT IS THE LOOP BOUND, unconditionally, and that is the whole point
  # of writing it this way. The previous version bounded itself with `rm -rf; continue`,
  # which skipped BOTH the budget check and the sleep - so when mkdir failed for any
  # reason the rm could not clear, it span forever, forking `date` and `cat` every
  # turn. Measured: it did not return in TEN MINUTES at MM_DEADLINE_SECS=20. A Stop
  # hook that never returns is worse than the silence this item exists to end, and a
  # loop whose termination depends on the filesystem behaving is not bounded at all.
  # Nothing below uses `continue`; every path falls through to the sleep.
  while [ "$_i" -lt "$LOCK_MAX_TRIES" ]; do
    _i=$(( _i + 1 ))
    # THE MAP FIRST, BECAUSE READING IT IS FREE AND `mkdir` IS A PROCESS. A run
    # waiting here wants the ROOT, not the lock, and once the winner has published
    # one there is nothing left to compete for - so checking costs nothing and
    # saves a syscall per turn. With the lock attempted first, every losing run
    # spent up to eight spawns discovering it had already lost.
    map_root
    if [ -n "$MAP_ROOT" ]; then root="$MAP_ROOT"; lock_done 1; return 1; fi
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
    # THE BACKSTOP, not the bound. Only a run that has already lost most of its
    # budget elsewhere reaches this, and for such a run an unthreaded message
    # beats a threaded silence.
    #
    # PROPORTIONAL, because a fixed 3 is the entire budget when MM_DEADLINE_SECS is
    # small: it fired on the first turn, the loop exited before the blind expiry
    # could ever run, and an `at`-less lock survived every run - measured at
    # MM_DEADLINE_SECS=3, leftover_lock=YES on five of seven cases.
    _backstop=$(( MM_DEADLINE_SECS / 2 ))
    [ "$_backstop" -gt 3 ] && _backstop=3
    [ "$_backstop" -lt 1 ] && _backstop=1
    if [ "$(remaining)" -le "$_backstop" ]; then
      # ON THE WAY OUT, BREAK A LOCK NOBODY CAN BE HOLDING. At a small budget the
      # backstop ends this loop on the first turn, so the blind expiry above -
      # which needs LOCK_BLIND_TRIES turns - never fires, and an `at`-less lock
      # survived EVERY run and silently disabled threading for that session. A
      # tester measured that at MM_DEADLINE_SECS=5. Clamping the two try-counts
      # against each other did not help, because the loop does not end at the try
      # count, it ends here. Removing it costs one `rm` and cannot orphan a live
      # holder: a live holder has a readable `at`.
      # Unreadable OR dated in the FUTURE. A future timestamp is all digits, so
      # a test that only looked for non-numeric text left that one case holding
      # the lock forever - the single survivor when the rest were fixed. `_age`
      # is negative for both, which is the property that actually matters: no
      # live holder can have written a start time this run has not reached.
      [ "$_age" -lt 0 ] && rm -rf "$_d" 2>/dev/null
      lock_done 1
      return 1
    fi
    sleep 0.2 2>/dev/null || sleep 1
  done
  lock_done 1
  return 1
}

# The lookup MUST happen inside the lock: a run that loses the race re-reads the
# map here and finds the winner's root, instead of announcing a second one.
lock_take || :

# 3) Find this session's thread root, if it has one.
root=""
if [ -n "$key" ] && [ -f "$THREADS" ]; then
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
out=$(post "${MENTION:+$MENTION }$MSG" "$root")
if [ -n "$out" ] && [ -n "$_make_root" ] && [ -n "$key" ]; then
  { printf '%s %s\n' "$key" "$out" >> "$THREADS"; } 2>/dev/null
fi

# The thread now exists, or the post failed and there is nothing to publish.
# Either way the next run may proceed.
[ -n "$_make_root" ] && lock_release

# 6) A root that no longer exists (post deleted, channel purged) must not wedge the session
#    into silence for the rest of its life. Drop the stale mapping and retry as a new thread,
#    once. Anything still failing after that is Mattermost's problem, not this turn's.
# A send that never happened for want of time is not evidence that the root is
# dead, and recovering from it costs calls we already know we cannot afford.
#
# THIS USED TO TEST A FLAG SET INSIDE `post`, AND THAT FLAG NEVER ARRIVED. `post`
# is always called as `$(post ...)` - a command substitution is a SUBSHELL, so its
# assignments die with it and the guard was dead code from the moment it was
# written. A tester proved it by instrumenting the caller. The budget is readable
# here directly, in this shell, which needs no flag at all: if there is not enough
# left to have made the call, the empty result says nothing about the root.
if [ -z "$out" ] && [ -n "$root" ] && [ "$(remaining)" -ge 2 ]; then
  if [ -n "$key" ] && [ -f "$THREADS" ]; then
    # awk, not `grep -v && mv`: when the map holds ONLY this session's line grep
    # exits 1, the && short-circuits, the mv never runs and the stale entry
    # survives - so the append below produced a DUPLICATE and the dead root was
    # still found first. Found in test, attempt 1.
    # NOTHING TO DO. The stale line is simply superseded by the one appended
    # below, because the lookup takes the LAST match. No rewrite, no temp file,
    # no race, and no `.tmp` orphans to gitignore.
    :
  fi
  # Same read-then-act as step 4, so the same lock. Without it two concurrent
  # recoveries of one session re-announce twice, which is the T15 defect wearing
  # a different hat.
  lock_take || :
  map_root; root="$MAP_ROOT"
  # Same invariant as step 4: without the lock we do not open a thread. A recovery
  # that cannot take it retries the message flat rather than racing a second root.
  if [ -z "$LOCK" ]; then
    # Post FLAT and stop. This used to send here and then fall through to the
    # unconditional send below, so every recovery that could not take the lock
    # delivered the operator the SAME message twice - measured 3 of 3 runs by a
    # tester, and invisible to every case in the plan because they all counted
    # "at least N" rather than "exactly N".
    root=""
    post "${MENTION:+$MENTION }$MSG" "" >/dev/null 2>&1
    _recovered=1
  elif [ -n "$root" ] && [ "$root" != "$_dead_root" ]; then
    # Another run already recovered this session while we waited. Use its root
    # rather than opening a third thread.
    lock_release
  else
    # Same rule as the first notification: the retry itself becomes the new root.
    # No header, so a recovery costs ONE call, which is what makes it affordable
    # at the moment we have already spent a call discovering the root was dead.
    root=""
    _retry=$(post "${MENTION:+$MENTION }$MSG" "")
    if [ -n "$_retry" ] && [ -n "$key" ]; then
      { printf '%s %s\n' "$key" "$_retry" >> "$THREADS"; } 2>/dev/null
    fi
    _recovered=1
    lock_release
  fi
  [ -z "$_recovered" ] && post "${MENTION:+$MENTION }$MSG" "$root" >/dev/null 2>&1
fi

exit 0
