#!/usr/bin/env bash
# zombie-sweep.sh [--kill]
#
# Lists — and with --kill, ends — this machine's zombie sessions: claude processes still running for sessions
# whose registry row already says `ended` (session-handoff ran, the pane was never closed — #655). Such a process
# pins its transcript's mtime, so a "move transcripts idle > N days" cleanup matches nothing and a dedicated
# transcript volume fills, while its heartbeat loop keeps touching a row nobody works on.
#
# The mapping comes from the heartbeat logs ($TMPDIR/ai-baton-<uid>/heartbeat-<key>.log, heartbeat.sh): each
# log's last `start name=<name> owner=<pid>` line names the session and its owning claude process. One line per
# log on stdout, then a summary:
#   ZOMBIE <name> owner=<pid> comm=<comm> row=<ended|archived>   owner alive, its heartbeat loop alive, row ended
#   LIVE <name> owner=<pid> …                                      row active (any status but ended), or this session
#   UNREGISTERED <name> owner=<pid>                                no row in sessions/ or sessions/archive/
#   STALE <name> owner=<pid> …                                     the loop is gone: the log may predate a reboot
#                                                                  and the pid belong to anyone
#   REUSED <name> owner=<pid> comm=<comm>                          the pid's comm is no longer claude-shaped
#   DEAD <name> owner=<pid>                                        owner gone (the loop ends the row itself)
#   UNMAPPED <log>                                                 no start line
# Exit 3 when at least one ZOMBIE was listed (list mode), else 0.
# --kill sends SIGTERM to the ZOMBIE owners only — never to an unmapped, stale, reused, dead or unregistered pid
# (an unregistered pane may be the user's own terminal) and never to this script's own ancestry — printing
# KILLED / KILL FAILED per row; exit 1 when a kill failed, else 0. The heartbeats then end the rows on their own
# (final stats written) within a minute.
# CONTEXT_ROOT overrides the workspace's .context/ root; TMPDIR the log dir, exactly as for heartbeat.sh.
set -u
KITDIR=$(cd "$(dirname "$0")/../.." && pwd)
kill_mode=0
for a in "$@"; do
  case "$a" in
    --kill) kill_mode=1 ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "usage: zombie-sweep.sh [--kill]" >&2; exit 2 ;;
  esac
done
CTX=${CONTEXT_ROOT:-$(python3 "$KITDIR/context-db/bin/kit_profile.py" context 2>/dev/null)}
[ -n "$CTX" ] && [ -d "$CTX/sessions" ] || { echo "zombie-sweep: no sessions/ under '$CTX' (set CONTEXT_ROOT)" >&2; exit 2; }
BATON_TMP="${TMPDIR:-/tmp}/ai-baton-$(id -u 2>/dev/null || echo 0)"

# This script's own ancestry — never a kill target, whatever a log says.
anc=" "; p=$$
while [ -n "$p" ] && [ "$p" != 0 ] && [ "$p" != 1 ]; do
  anc="$anc$p "; p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
done

comm_of() { ps -o comm= -p "$1" 2>/dev/null | tr -d ' '; }   # empty = no such process; comm only, never argv

# row_status <name>: the row's `status:` (empty status reads `unknown`), `archived` for a row only under
# sessions/archive/ (plain, or gen_sessions' <name>-YYYYMMDD[-n] / <name>-undated[-n] shapes), '' for no row.
row_status() {
  local f="$CTX/sessions/$1.md" s g
  if [ -f "$f" ]; then
    s=$(sed -n '2,/^---$/{s/^status:[[:space:]]*//p;}' "$f" | head -n 1 | tr -d '"'"'"' ')
    echo "${s:-unknown}"; return
  fi
  for g in "$CTX/sessions/archive/$1.md" "$CTX/sessions/archive/$1"-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].md \
           "$CTX/sessions/archive/$1"-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9]*.md \
           "$CTX/sessions/archive/$1"-undated.md "$CTX/sessions/archive/$1"-undated-[0-9]*.md; do
    [ -f "$g" ] && { echo archived; return; }
  done
  echo ""
}

zombies=0; live=0; unreg=0; stale=0; reused=0; unmapped=0; dead=0; failed=0; seen=" "
for log in "$BATON_TMP"/heartbeat-*.log; do
  [ -f "$log" ] || continue
  last=$(grep -F ' start name=' "$log" 2>/dev/null | tail -n 1)
  name=""; owner=""
  case "$last" in *" start name="*" owner="*)
    name=${last##* start name=}; name=${name%% *}
    owner=${last##* owner=}; owner=${owner%% *};;
  esac
  case "$owner" in ''|*[!0-9]*) unmapped=$((unmapped+1)); echo "UNMAPPED ${log##*/}"; continue;; esac
  case "$seen" in *" $owner "*) continue;; esac   # a second log for the same owner (another key): one verdict per process
  seen="$seen$owner "
  comm=$(comm_of "$owner")
  [ -n "$comm" ] || { dead=$((dead+1)); echo "DEAD $name owner=$owner"; continue; }
  loop=$(cat "${log%.log}.pid" 2>/dev/null)
  case "$loop" in ''|*[!0-9]*) loop="";; esac
  if [ -z "$loop" ] || [ -z "$(comm_of "$loop")" ]; then
    stale=$((stale+1)); echo "STALE $name owner=$owner (heartbeat loop gone — the log may predate a reboot)"; continue
  fi
  case "$comm" in claude|node|[0-9]*.[0-9]*.[0-9]*) ;; *) reused=$((reused+1)); echo "REUSED $name owner=$owner comm=$comm"; continue;; esac
  case "$anc" in *" $owner "*) live=$((live+1)); echo "LIVE $name owner=$owner (this session)"; continue;; esac
  st=$(row_status "$name")
  case "$st" in
    ended|archived) ;;
    '') unreg=$((unreg+1)); echo "UNREGISTERED $name owner=$owner"; continue;;
    *) live=$((live+1)); echo "LIVE $name owner=$owner row=$st"; continue;;
  esac
  zombies=$((zombies+1))
  if [ "$kill_mode" = 1 ]; then
    if kill -TERM "$owner" 2>/dev/null; then echo "KILLED $name owner=$owner comm=$comm row=$st"
    else failed=$((failed+1)); echo "KILL FAILED $name owner=$owner comm=$comm row=$st"; fi
  else
    echo "ZOMBIE $name owner=$owner comm=$comm row=$st"
  fi
done
echo "zombie-sweep: $zombies zombie(s), $live live, $unreg unregistered, $stale stale, $reused reused, $dead dead, $unmapped unmapped"
if [ "$kill_mode" = 1 ]; then [ "$failed" = 0 ] || exit 1; exit 0; fi
[ "$zombies" = 0 ] || exit 3
exit 0
