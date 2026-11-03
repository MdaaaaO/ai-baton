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
# gone counts only when none of the owner's logs has a live loop: only a live loop vouches for its mapping.
# One line per owner on stdout — <names> are the session names its live-loop logs carry, comma-joined, and
# row= their statuses in the same order (`none` for no row) — then a summary:
#   ZOMBIE <names> owner=<pid> comm=<comm> row=<ended|archived,…>  owner alive, a heartbeat loop alive, every
#                                                                  row ended or archived
#   LIVE <names> owner=<pid> row=<…>                               any row active (any status but ended), or
#                                                                  this session
#   UNREGISTERED <names> owner=<pid> row=<…>                       a name with no row in sessions/ or
#                                                                  sessions/archive/, and none active
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
  case "$comm" in claude|node|[0-9]*.[0-9]*.[0-9]*) ;; *) reused=$((reused+1)); echo "REUSED $fnames owner=$owner comm=$comm"; continue;; esac
  case "$anc" in *" $owner "*) live=$((live+1)); echo "LIVE $fnames owner=$owner (this session)"; continue;; esac
  # the rows behind those names: one active row keeps the owner LIVE, else one missing row UNREGISTERED,
  # else every row ended or archived — a ZOMBIE
  rows=""; active=0; missing=0; rest=$fnames
  while [ -n "$rest" ]; do
    nm=${rest%%,*}
    if [ "$nm" = "$rest" ]; then rest=""; else rest=${rest#*,}; fi
    st=$(row_status "$nm")
    case "$st" in
      ended|archived) ;;
      '') missing=1; st=none;;
      *) active=1;;
    esac
    rows="$rows${rows:+,}$st"
  done
  if [ "$active" = 1 ]; then live=$((live+1)); echo "LIVE $fnames owner=$owner row=$rows"; continue; fi
  if [ "$missing" = 1 ]; then unreg=$((unreg+1)); echo "UNREGISTERED $fnames owner=$owner row=$rows"; continue; fi
  zombies=$((zombies+1))
  if [ "$kill_mode" = 1 ]; then
    if kill -TERM "$owner" 2>/dev/null; then echo "KILLED $fnames owner=$owner comm=$comm row=$rows"
    else failed=$((failed+1)); echo "KILL FAILED $fnames owner=$owner comm=$comm row=$rows"; fi
  else
    echo "ZOMBIE $fnames owner=$owner comm=$comm row=$rows"
  fi
done
echo "zombie-sweep: $zombies zombie(s), $live live, $unreg unregistered, $stale stale, $reused reused, $dead dead, $unmapped unmapped"
if [ "$kill_mode" = 1 ]; then [ "$failed" = 0 ] || exit 1; exit 0; fi
[ "$zombies" = 0 ] || exit 3
exit 0
