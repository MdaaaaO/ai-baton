#!/usr/bin/env bash
# sync.sh — keep the .claude kit in step with origin, PR-only: the kit is PULLED (fast-forward of
# `main`), never committed or pushed from here. Kit changes travel branch → PR → merge on GitHub;
# a versioned pre-push hook (hooks/pre-push, installed via core.hooksPath by this script and
# setup.sh) refuses any push to the kit's `main`. Nothing else is synced: an environment's facts
# live in the local env store (.context/reference/env/, never in git), its prose in
# .context/reference/environment.md. Idempotent, lock-guarded, never fails the caller (it is also
# run from a SessionEnd hook). Takes no arguments.
#
#   sh .claude/sync.sh                  (from the workspace root, or via `make claude_sync`)
#
# Outcome is recorded in .sync-status (ignored; one line: `<utc-ts> <state> <detail>`) so a later
# session can see what a background sync did — the SessionEnd hook swallows all output.
# `sync-check.sh` (run by `make -C .claude/context-db session-register`) reads it. States:
#   pending <epoch> …           written just before the fetch; still there later = the run was killed
#   ok <what>                   fetched; fast-forwarded or already in step
#   offline <epoch> since <ts>  the fetch could not resolve/reach origin; <epoch> = first run of the streak
#   error <reason>              needs the user: off main, dirty, ahead, fetch failed or timed out, ff failed
# A run that finds the lock busy logs `skipped` and leaves .sync-status alone (the holder writes it).
# The sync reports `error` — and pulls nothing — when .claude/ is not on main, has uncommitted
# changes, or carries local commits on main: each of those is work that must move to a branch + PR.
set -u
FETCH_TIMEOUT="${SYNC_FETCH_TIMEOUT:-60}"  # seconds; Claude Code does not enforce an async hook's `timeout` (docs/sync.md)
LOCK_WAIT="${SYNC_LOCK_WAIT:-30}"          # seconds to wait for another sync.sh's flock
LOG_KEEP=200                               # sync.log is trimmed to its last LOG_KEEP lines

HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$HERE/sync.log"
STATUS="$HERE/.sync-status"
FAIL=""; PULLED=""; OFFLINE=""; ERRF=""

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG"; }
status() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >"$STATUS"; }
# " (v0.3.0)" on a release commit, " (v0.3.0+2)" two commits after it, "" before the first release tag
kit_version() {
  d="$(git describe --tags --match 'v[0-9]*' --long 2>/dev/null)" || return 0
  n="${d%-g*}"; n="${n##*-}"; t="${d%-*-g*}"
  [ "$n" = 0 ] && printf ' (%s)' "$t" || printf ' (%s+%s)' "$t" "$n"
}

# with_timeout <secs> <cmd…> — GNU `timeout` (Linux), `gtimeout` (macOS coreutils), else a shell
# watchdog that polls once a second and TERMs the command. Exit 124 when the time ran out.
with_timeout() {
  local secs=$1 pid wd rc; shift
  if command -v timeout >/dev/null 2>&1; then timeout "$secs" "$@"; return; fi
  if command -v gtimeout >/dev/null 2>&1; then gtimeout "$secs" "$@"; return; fi
  "$@" & pid=$!
  ( n=0; while [ "$n" -lt "$secs" ]; do sleep 1; kill -0 "$pid" 2>/dev/null || exit 0; n=$((n + 1)); done
    kill -TERM "$pid" 2>/dev/null ) >/dev/null 2>&1 9>&- &
  wd=$!
  wait "$pid"; rc=$?
  kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
  [ "$rc" -eq 143 ] && rc=124
  return "$rc"
}

# fetch_offline <stderr-file> — did the fetch fail because origin could not be resolved or reached?
fetch_offline() {
  grep -Eqi 'could not resolve (host|hostname)|temporary failure in name resolution|name or service not known|nodename nor servname|failed to connect|couldn.t connect to server|network is unreachable|no route to host|connection (refused|timed out)|operation timed out' "$1"
}

# sync_kit — PR-only: install the pre-push guard, then fast-forward main to origin/main when the
# checkout is clean. Never stages, commits or pushes. Sets FAIL (and pulls nothing) when the
# checkout is off main, dirty, or ahead of origin — that work belongs on a branch + PR; sets
# OFFLINE when origin could not be reached.
sync_kit() {
  cd "$HERE" || return 1
  if [ "$(git config --get core.hooksPath 2>/dev/null)" != "$HERE/hooks" ]; then
    git config core.hooksPath "$HERE/hooks" >>"$LOG" 2>&1 && log "kit: installed git hooks pre-push + commit-msg (core.hooksPath=$HERE/hooks)"
  fi
  local branch; branch="$(git symbolic-ref -q --short HEAD 2>/dev/null || echo DETACHED)"
  if [ "$branch" != "main" ]; then
    log "kit: ERROR checked out on '$branch' — .claude/ must stay on main"
    FAIL="kit is on '$branch' — .claude/ must stay on main (git switch main); branch work lives in a worktree under .worktrees/"
    return 1
  fi
  if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    log "kit: ERROR uncommitted changes in .claude/ — pull skipped (main is PR-only)"
    FAIL="kit has uncommitted changes in .claude/ — main is PR-only: move them to a branch worktree (git worktree add .worktrees/kit_<topic> -b <topic>) and open a PR"
    return 1
  fi
  git remote get-url origin >/dev/null 2>&1 || { log "kit: skip, no origin"; return 0; }
  # pending first: a run killed mid-fetch (terminal closed, forced exit) leaves this, not the previous `ok`
  status "pending $(date -u +%s) fetch started (pid $$) — still here minutes later = the run was killed"
  local rc=0
  ERRF="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/kit-sync.$$")"
  with_timeout "$FETCH_TIMEOUT" git fetch -q origin 2>"$ERRF" 9>&- || rc=$?
  [ -s "$ERRF" ] && cat "$ERRF" >>"$LOG"
  if [ "$rc" -eq 124 ]; then
    log "kit: ERROR fetch timed out after ${FETCH_TIMEOUT}s"
    FAIL="kit fetch timed out after ${FETCH_TIMEOUT}s (hung network or a credential prompt? see sync.log)"; return 1
  elif [ "$rc" -ne 0 ] && fetch_offline "$ERRF"; then
    log "kit: offline — origin unreachable, nothing pulled"; OFFLINE=1; return 0
  elif [ "$rc" -ne 0 ]; then
    log "kit: ERROR fetch failed (exit $rc)"; FAIL="kit fetch failed (see sync.log)"; return 1
  fi
  local ahead; ahead="$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)"
  if [ "$ahead" -gt 0 ]; then
    log "kit: ERROR $ahead local commit(s) on main not on origin — main is PR-only, nothing pushed"
    FAIL="kit has $ahead local commit(s) on main that will never be pushed — main is PR-only: git branch <topic> && git reset --hard origin/main, then open a PR from <topic>"
    return 1
  fi
  local before; before="$(git rev-parse --short HEAD 2>/dev/null)"
  if git merge -q --ff-only origin/main >>"$LOG" 2>&1; then
    PULLED="kit@$(git rev-parse --short HEAD)"
    # the sha line only when HEAD moved: an in-step sync at every session end logs nothing
    [ "kit@$before" = "$PULLED" ] || log "kit: main at $(git rev-parse --short HEAD)$(kit_version) (was $before)"
  else
    log "kit: ERROR fast-forward failed"; FAIL="kit fast-forward to origin/main failed (see sync.log)"; return 1
  fi
}

# the cheap check first: no repo means no lock, no status
[ -d "$HERE/.git" ] || { log "skip: .claude is not a git repo"; exit 0; }

# lock: flock where it exists (Linux); on hosts without it (macOS without coreutils) an atomic mkdir
# lock, treated as stale after 10 minutes. A busy lock is logged as `skipped` (and said on stderr for
# `make claude_sync`) but never written to .sync-status: the holder's pending/ok/error is newer.
LOCKDIR="$HERE/.sync.lock.d"; HAVE_LOCKDIR=""
cleanup() { [ -n "$ERRF" ] && rm -f "$ERRF"; [ -n "$HAVE_LOCKDIR" ] && rmdir "$LOCKDIR" 2>/dev/null; return 0; }
trap cleanup EXIT
skipped() { log "skipped: lock busy — $*"; printf 'sync.sh: skipped, lock busy — %s\n' "$*" >&2; }
take_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$HERE/.sync.lock"
    flock -w "$LOCK_WAIT" 9 && return 0
    skipped "another sync.sh holds .sync.lock (hung SessionEnd sync? \`pgrep -af sync.sh\`, kill it)"
  else
    if mkdir "$LOCKDIR" 2>/dev/null; then HAVE_LOCKDIR=1; return 0; fi
    if [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +10 2>/dev/null)" ]; then
      rmdir "$LOCKDIR" 2>/dev/null
      if mkdir "$LOCKDIR" 2>/dev/null; then log "lock: removed a stale lock dir (>10 min)"; HAVE_LOCKDIR=1; return 0; fi
    fi
    skipped "no flock on this host and $LOCKDIR exists (another sync running; \`rmdir\` it if none is)"
  fi
  return 1
}
take_lock || exit 0

# sync.log keeps its last LOG_KEEP lines, trimmed in place (a temp file outside the checkout, never
# a second file inside it — an untracked file would make the next sync refuse to pull)
if [ -f "$LOG" ] && [ "$(wc -l <"$LOG")" -gt "$LOG_KEEP" ]; then
  if t="$(mktemp 2>/dev/null)"; then tail -n "$LOG_KEEP" "$LOG" >"$t" && cat "$t" >"$LOG"; rm -f "$t"; fi
fi

# an offline streak keeps the epoch of its first offline run, so sync-check can tell a day from a week
since=""
if [ -f "$STATUS" ]; then
  read -r _ prev_state prev_since _ <"$STATUS" || true
  [ "${prev_state:-}" = offline ] && case "${prev_since:-}" in ''|*[!0-9]*) ;; *) since=$prev_since ;; esac
fi

sync_kit

if [ -n "$FAIL" ]; then status "error $FAIL"
elif [ -n "$OFFLINE" ]; then
  since="${since:-$(date -u +%s)}"
  # BSD date takes -r <epoch>, GNU date -d @<epoch>
  since_ts="$(date -u -r "$since" +%FT%TZ 2>/dev/null || date -u -d "@$since" +%FT%TZ 2>/dev/null || echo "$since")"
  status "offline $since since $since_ts — origin unreachable, nothing pulled"
else status "ok ${PULLED:-kit no-origin}"; fi
exit 0
