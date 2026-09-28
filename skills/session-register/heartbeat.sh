#!/usr/bin/env bash
# heartbeat.sh <session-name> ["<working on>"] [interval-seconds]
#
# Zero-model-turn heartbeat for the live session registry (.context/SESSION_INDEX.md).
# Run it ONCE from the owning Claude session as a plain Bash tool call:
#     bash $BATON/skills/session-register/heartbeat.sh <name> "<focus>"
# It resolves the owning `claude` process from its own ancestry, then re-executes itself detached
# (skills/_lib/portable.sh's detach — the Bash tool kills its process group after ~10 min otherwise) and returns.
# The detached copy touches the registry row every INTERVAL (default 6h) while that claude process
# is alive, checks liveness every 60 s, and marks the row `ended` the minute the session is gone —
# so a heartbeat never outlives its session and the registry never lies about liveness.
#
# Every touch also refreshes the row's `stats:` line (turns, context, tokens, rough spend, PRs /
# tickets / sign jobs / drafts) from the session's transcript — session.py derives it via
# session_stats.py from $CLAUDE_CODE_SESSION_ID, which this script captures from the Bash tool's
# environment and hands to the detached copy explicitly. The final `session-end` on session death
# writes the `## Session stats` block into the session file and a row into sessions/_ledger.md,
# so even a session that never ran session-handoff leaves its numbers behind.
# Log: $TMPDIR/ai-baton-<uid>/heartbeat-<name>.log · pidfile: same dir, heartbeat-<name>.pid (second
# start = no-op). Namespaced under TMPDIR (a per-user dir on macOS already; `ai-baton-<uid>` makes it
# one on Linux too, where TMPDIR is usually unset and bare /tmp is shared between users).
TZ_DEFAULT=$(python3 "$(dirname "$0")/../../context-db/bin/kit_profile.py" tz 2>/dev/null || echo UTC)  # identity-aware: plugin option, else WORKSPACE_TZ, else the store
set -u
NAME=${1:?usage: heartbeat.sh <session-name> ["<working on>"] [interval-seconds]}
WORKING=${2:-}
INTERVAL=${3:-21600}
KITDIR=$(cd "$(dirname "$0")/../.." && pwd)  # the kit itself: a .claude/ clone or the plugin root (#3)
# shellcheck source=../_lib/portable.sh
. "$KITDIR/skills/_lib/portable.sh"  # detach — GNU/Linux and macOS/BSD (no setsid) alike
MK="make -s -C $KITDIR/context-db"
BATON_TMP=${TMPDIR:-/tmp}/ai-baton-$(id -u 2>/dev/null || echo 0)
mkdir -p "$BATON_TMP" 2>/dev/null
LOG=$BATON_TMP/heartbeat-$NAME.log
PIDFILE=$BATON_TMP/heartbeat-$NAME.pid
SESSION_ID=${CLAUDE_CODE_SESSION_ID:-}

if [ -z "${HEARTBEAT_DETACHED:-}" ]; then
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "heartbeat for $NAME already running (pid $(cat "$PIDFILE"))"; exit 0
  fi
  # Walk up from this shell to the claude process that owns the session.
  p=$$; CLAUDE_PID=""
  while [ -n "$p" ] && [ "$p" != "0" ] && [ "$p" != "1" ]; do
    c=$(ps -o comm= -p "$p" 2>/dev/null | tr -d ' ')   # comm only — never cmd/aux (argv leaks tokens)
    case "$c" in claude|node) CLAUDE_PID=$p; break;; esac
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
  done
  [ -n "$CLAUDE_PID" ] || { echo "heartbeat: could not find the owning claude process" >&2; exit 1; }
  [ -n "$SESSION_ID" ] || echo "heartbeat: CLAUDE_CODE_SESSION_ID unset — the row will carry no stats" >&2
  export HEARTBEAT_DETACHED=1 CLAUDE_PID CLAUDE_CODE_SESSION_ID=$SESSION_ID
  detach "$LOG" bash "$0" "$NAME" "$WORKING" "$INTERVAL"
  echo "heartbeat for $NAME started (owner claude pid $CLAUDE_PID, every ${INTERVAL}s, stats ${SESSION_ID:+on}${SESSION_ID:-off}, log $LOG)"
  exit 0
fi

echo $$ > "$PIDFILE"
echo "$(TZ=$TZ_DEFAULT date +'%Y-%m-%d %I:%M %p %Z') start name=$NAME owner=$CLAUDE_PID interval=$INTERVAL session=${SESSION_ID:-none}"
first=1
while kill -0 "$CLAUDE_PID" 2>/dev/null; do
  if [ $first = 1 ] && [ -n "$WORKING" ]; then
    $MK session-touch NAME="$NAME" WORKING="$WORKING"
  else
    $MK session-touch NAME="$NAME"
  fi
  first=0
  slept=0
  while [ "$slept" -lt "$INTERVAL" ] && kill -0 "$CLAUDE_PID" 2>/dev/null; do sleep 60; slept=$((slept+60)); done
done
echo "$(TZ=$TZ_DEFAULT date +'%Y-%m-%d %I:%M %p %Z') owner $CLAUDE_PID gone — ending $NAME"
$MK session-end NAME="$NAME"
rm -f "$PIDFILE"
