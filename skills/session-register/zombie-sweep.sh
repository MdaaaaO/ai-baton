#!/usr/bin/env bash
# zombie-sweep.sh [--kill]
#
# Lists — and with --kill, ends — this machine's zombie sessions: claude processes still running for sessions
# whose registry row already says `ended` (session-handoff ran, the pane was never closed — #655). Such a process
# pins its transcript's mtime, so a "move transcripts idle > N days" cleanup matches nothing and a dedicated
# transcript volume fills, while its heartbeat loop keeps touching a row nobody works on.
#
# The mapping comes from the heartbeat logs ($TMPDIR/ai-baton-<uid>/heartbeat-<key>.log, heartbeat.sh): each
# log's last `start name=<name> owner=<pid>` line names the session and its owning claude process. One process
# may own several logs (a handoff, then a new session registered in the same pane; an earlier key), so the
# verdict is per owner, judged across every log that names it — never per log. A log whose heartbeat loop is
# gone never vouches for a kill (no live loop at all reads STALE), yet an active row it names still keeps the
# owner LIVE — a loop can die under a working session — while a missing row from it proves nothing (a name
# renamed away, a log from before a reboot). One line per owner on stdout — <names> are every session name
# its logs carry, comma-joined, and row= their statuses in the same order (`none` for no row) — then a summary:
#   ZOMBIE <names> owner=<pid> comm=<comm> row=<ended|archived,…>  owner alive, a heartbeat loop alive, every
#                                                                  row ended or archived (a dead-loop log's
#                                                                  missing one aside)
#   LIVE <names> owner=<pid> row=<…>                               any row active (any status but ended), or
#                                                                  this session
#   UNREGISTERED <names> owner=<pid> row=<…>                       a live-loop name with no row in sessions/
#                                                                  or sessions/archive/, and none active
#   STALE <names> owner=<pid> …                                    every loop is gone: the logs may predate a
#                                                                  reboot and the pid belong to anyone
#   REUSED <names> owner=<pid> comm=<comm>                         the pid's comm is no longer claude-shaped
#   DEAD <names> owner=<pid>                                       owner gone (the loop ends the row itself)
#   UNMAPPED <log>                                                 no start line
# Exit 3 when at least one ZOMBIE was listed (list mode), else 0.
# --kill sends SIGTERM to the ZOMBIE owners only — never to an unmapped, stale, reused, dead or unregistered pid
# (an unregistered pane may be the user's own terminal), never to an owner with any active row, and never to
# this script's own ancestry — printing KILLED / KILL FAILED per owner; exit 1 when a kill failed, else 0. The
# heartbeats then end the rows on their own (final stats written) within a minute.
# Leases: list mode names what a sweep would free ("would free", no write); --kill frees them.
# CONTEXT_ROOT overrides the workspace's .context/ root; TMPDIR the log dir, exactly as for heartbeat.sh.
set -u
KITDIR=$(cd "$(dirname "$0")/../.." && pwd)
kill_mode=0
for a in "$@"; do
  case "$a" in
    --kill) kill_mode=1 ;;
    -h|--help) awk 'NR > 1 && /^set -u$/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
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

# Pass 1 — every mapped log, in glob order: its owner, its name, whether its heartbeat loop still runs.
owners=(); names=(); fresh=(); unmapped=0
for log in "$BATON_TMP"/heartbeat-*.log; do
  [ -f "$log" ] || continue
  last=$(grep -F ' start name=' "$log" 2>/dev/null | tail -n 1)
  name=""; owner=""
  case "$last" in *" start name="*" owner="*)
    name=${last##* start name=}; name=${name%% *}
    owner=${last##* owner=}; owner=${owner%% *};;
  esac
  case "$owner" in ''|*[!0-9]*) unmapped=$((unmapped+1)); echo "UNMAPPED ${log##*/}"; continue;; esac
  loop=$(cat "${log%.log}.pid" 2>/dev/null)
  case "$loop" in ''|*[!0-9]*) loop="";; esac
  f=0; [ -n "$loop" ] && [ -n "$(comm_of "$loop")" ] && f=1
  owners+=("$owner"); names+=("$name"); fresh+=("$f")
done

# Pass 2 — one verdict per owner, first-seen order, over every log that names it.
zombies=0; live=0; unreg=0; stale=0; reused=0; dead=0; failed=0; seen=" "
total=${#owners[@]}
for ((i = 0; i < total; i++)); do
  owner=${owners[$i]}
  case "$seen" in *" $owner "*) continue;; esac
  seen="$seen$owner "
  fnames=""; anames=""   # the names its live-loop logs carry; every name it was ever mapped to (deduped)
  for ((j = i; j < total; j++)); do
    [ "${owners[$j]}" = "$owner" ] || continue
    nm=${names[$j]}
    case ",$anames," in *",$nm,"*) ;; *) anames="$anames${anames:+,}$nm";; esac
    [ "${fresh[$j]}" = 1 ] || continue
    case ",$fnames," in *",$nm,"*) ;; *) fnames="$fnames${fnames:+,}$nm";; esac
  done
  comm=$(comm_of "$owner")
  [ -n "$comm" ] || { dead=$((dead+1)); echo "DEAD $anames owner=$owner"; continue; }
  [ -n "$fnames" ] || { stale=$((stale+1)); echo "STALE $anames owner=$owner (heartbeat loop gone — the log may predate a reboot)"; continue; }
  case "$comm" in claude|node|[0-9]*.[0-9]*.[0-9]*) ;; *) reused=$((reused+1)); echo "REUSED $anames owner=$owner comm=$comm"; continue;; esac
  case "$anc" in *" $owner "*) live=$((live+1)); echo "LIVE $anames owner=$owner (this session)"; continue;; esac
  # the rows behind every name: one active row keeps the owner LIVE — a dead-loop log's too, a loop can die
  # under a working session — else one live-loop name with no row UNREGISTERED (a dead-loop log's missing row
  # proves nothing: a name renamed away, a log from before a reboot), else every row ended or archived — a ZOMBIE
  rows=""; active=0; missing=0; rest=$anames
  while [ -n "$rest" ]; do
    nm=${rest%%,*}
    if [ "$nm" = "$rest" ]; then rest=""; else rest=${rest#*,}; fi
    st=$(row_status "$nm")
    case "$st" in
      ended|archived) ;;
      '') st=none; case ",$fnames," in *",$nm,"*) missing=1;; esac;;
      *) active=1;;
    esac
    rows="$rows${rows:+,}$st"
  done
  if [ "$active" = 1 ]; then live=$((live+1)); echo "LIVE $anames owner=$owner row=$rows"; continue; fi
  if [ "$missing" = 1 ]; then unreg=$((unreg+1)); echo "UNREGISTERED $anames owner=$owner row=$rows"; continue; fi
  zombies=$((zombies+1))
  if [ "$kill_mode" = 1 ]; then
    if kill -TERM "$owner" 2>/dev/null; then echo "KILLED $anames owner=$owner comm=$comm row=$rows"
    else failed=$((failed+1)); echo "KILL FAILED $anames owner=$owner comm=$comm row=$rows"; fi
  else
    echo "ZOMBIE $anames owner=$owner comm=$comm row=$rows"
  fi
done
# Leases a dead or expired holder left behind: list mode only names what a sweep would free (lease.py sweep
# --dry-run), so a read never writes; --kill frees them (lease.py sweep, one line each).
lease_rc=0
if [ "$kill_mode" = 1 ]; then python3 "$KITDIR/context-db/bin/lease.py" sweep 2>/dev/null || lease_rc=$?
else python3 "$KITDIR/context-db/bin/lease.py" sweep --dry-run 2>/dev/null || lease_rc=$?; fi
[ "$lease_rc" = 0 ] || echo "lease sweep did not run (exit $lease_rc)"
echo "zombie-sweep: $zombies zombie(s), $live live, $unreg unregistered, $stale stale, $reused reused, $dead dead, $unmapped unmapped"
if [ "$kill_mode" = 1 ]; then [ "$failed" = 0 ] || exit 1; exit 0; fi
[ "$zombies" = 0 ] || exit 3
exit 0
