#!/bin/sh
# shellcheck shell=dash  # run as `sh` everywhere (README, workspace.mk); dash has `local`
# sync.sh — keep the .claude kit in step with origin, PR-only: the kit is PULLED (fast-forward),
# never committed or pushed from here. Kit changes travel branch → PR → merge on GitHub;
# a versioned pre-push hook (hooks/pre-push, installed via core.hooksPath by this script and
# setup.sh) refuses any push to the kit's `main`. Nothing else is synced: an environment's facts
# live in the local env store (.context/reference/env/, never in git), its prose in
# .context/reference/environment.md. Idempotent, lock-guarded; exits 0 except 3 when another run holds
# the lock (it is also run from a SessionEnd hook, which ignores the exit).
#
# Channel: by default the kit follows the highest `v[0-9]*` release tag that `origin/main` contains
# (it tracks `main` directly before the first release tag exists). `git config kit.channel main`
# opts a checkout back into the old behaviour — always fast-forward straight to `origin/main`. On the
# release channel a tag newer than HEAD is never applied by a bare run: it only writes the ignored
# .sync-preview (the commits, the changed skills/agents/hooks, and the other files a clone runs
# unattended) and the status `held <tag>`, so a SessionEnd run never applies an update unattended.
# The one optional argument, --accept, applies the held tag — passed by `make claude_sync`, never by
# the SessionEnd hook.
#
#   sh .claude/sync.sh [--accept]       (from the workspace root, or via `make claude_sync`)
#
# Outcome is recorded in .sync-status (ignored; one line: `<utc-ts> <state> <detail>`) so a later
# session can see what a background sync did — the SessionEnd hook swallows all output.
# `sync-check.sh` (run by `make -C .claude/context-db session-register`) reads it. States:
#   pending <epoch> …           written just before the fetch; still there later = the run was killed
#   ok <what>                   fetched; fast-forwarded, already in step, or already past the held tag
#   held <tag>                  a newer release tag is waiting in .sync-preview; `make claude_sync` applies it
#   offline <epoch> since <ts>  the fetch could not resolve/reach origin; <epoch> = first run of the streak
#   error <reason>              needs the user: off main, dirty, ahead, fetch failed or timed out, ff failed
# A run that finds the lock busy logs `skipped`, exits 3 and leaves .sync-status alone (the holder writes it).
# The sync reports `error` — and pulls nothing — when .claude/ is not on main, has uncommitted
# changes, or carries local commits on main: each of those is work that must move to a branch + PR.
set -u
FETCH_TIMEOUT="${SYNC_FETCH_TIMEOUT:-60}"  # seconds; Claude Code does not enforce an async hook's `timeout` (docs/sync.md)
LOCK_WAIT="${SYNC_LOCK_WAIT:-30}"          # seconds to wait for another sync.sh's flock
LOG_KEEP=200                               # sync.log is trimmed to its last LOG_KEEP lines

ACCEPT=""
for _arg in "$@"; do [ "$_arg" = "--accept" ] && ACCEPT=1; done

HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$HERE/sync.log"
STATUS="$HERE/.sync-status"
PREVIEW="$HERE/.sync-preview"
FAIL=""; PULLED=""; OFFLINE=""; HELD=""; ERRF=""

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

# release_tag — the highest `v[0-9]*` tag reachable from origin/main (the pattern kit_version()
# already matches), or empty before the first release tag.
release_tag() {
  git for-each-ref --merged origin/main --sort=-version:refname --format='%(refname:short)' \
    'refs/tags/v[0-9]*' 2>/dev/null | head -n 1
}

# ff_to <ref> <label> — fast-forward HEAD to <ref>; <label> (e.g. " (v1.2.3)") is appended to the
# log's sha line and to PULLED, or kit_version() is used when <label> is empty (the main channel).
ff_to() {
  local ref=$1 label=$2 before; before="$(git rev-parse --short HEAD 2>/dev/null)"
  if git merge -q --ff-only "$ref" >>"$LOG" 2>&1; then
    PULLED="kit@$(git rev-parse --short HEAD)$label"
    [ "kit@$before$label" = "$PULLED" ] || log "kit: main at $(git rev-parse --short HEAD)${label:-$(kit_version)} (was $before)"
    return 0
  fi
  log "kit: ERROR fast-forward to $ref failed"; FAIL="kit fast-forward to $ref failed (see sync.log)"; return 1
}

# write_preview <tag> <from> <to> — the ignored .sync-preview a held run writes instead of applying:
# the commits between HEAD and the tag, the changed skills/agents/hooks, and the other files a clone
# runs unattended (git hooks, settings.json, setup.sh, this script, the engine's own bin/).
write_preview() {
  local tag=$1 from=$2 to=$3
  {
    printf 'release %s held — `make claude_sync` applies it (or `sh sync.sh --accept`)\n\n' "$tag"
    printf 'commits:\n'
    git log --oneline "$from..$to" 2>/dev/null
    printf '\nchanged skills/agents/hooks:\n'
    git diff --name-only "$from" "$to" -- skills/ agents/ hooks/ 2>/dev/null
    printf '\nother files that run unattended:\n'
    git diff --name-only "$from" "$to" -- settings.json setup.sh sync.sh context-db/bin/ 2>/dev/null
  } >"$PREVIEW"
  log "kit: held $tag — wrote $(basename "$PREVIEW")"
}

# sync_release <tag> — the default (release) channel: nothing to do when HEAD is already at <tag> or
# already past it (e.g. a channel switch back from `main`); without --accept, holds (preview + status
# `held <tag>`, applies nothing); with --accept, fast-forwards to it.
sync_release() {
  local tag=$1 target before
  target="$(git rev-parse "refs/tags/$tag^{commit}" 2>/dev/null)"
  before="$(git rev-parse HEAD 2>/dev/null)"
  rm -f "$PREVIEW"
  if [ "$target" = "$before" ] || ! git merge-base --is-ancestor "$before" "$target" 2>/dev/null; then
    PULLED="kit@$(git rev-parse --short HEAD)"
    return 0
  fi
  if [ -z "$ACCEPT" ]; then
    write_preview "$tag" "$before" "$target"
    HELD="$tag"
    return 0
  fi
  ff_to "refs/tags/$tag" " ($tag)"
}

# sync_kit — PR-only: install the pre-push guard, then move main to the right target when the
# checkout is clean — the highest release tag `origin/main` contains by default (held until
# --accept), or `origin/main` itself with `kit.channel main` or before the first release tag. Never
# stages, commits or pushes. Sets FAIL (and pulls nothing) when the checkout is off main, dirty, or
# ahead of origin — that work belongs on a branch + PR; sets OFFLINE when origin could not be reached.
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
  with_timeout "$FETCH_TIMEOUT" git fetch -q --tags origin 2>"$ERRF" 9>&- || rc=$?
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
  local channel; channel="$(git config --get kit.channel 2>/dev/null || true)"
  if [ "$channel" != main ]; then
    local tag; tag="$(release_tag)"
    [ -n "$tag" ] && { sync_release "$tag"; return $?; }
  fi
  # the main channel, or the release channel before the first release tag exists: no tag to hold on
  rm -f "$PREVIEW"
  ff_to origin/main ""
}

# the cheap check first: no repo means no lock, no status
[ -d "$HERE/.git" ] || { log "skip: .claude is not a git repo"; exit 0; }

# lock: flock where it exists (Linux); on hosts without it (macOS without coreutils) an atomic mkdir
# lock. Either way the holder records `<pid> <utc-ts> <process-start-time>` (in .sync.lock, or
# .sync.lock.d/owner) — the third field is the owner's own process start time as `ps -o lstart=`
# reports it, so a stale lock can be told apart from a live one even after its pid gets reused (a
# reboot or pid wraparound): a lock dir is stale when its owner pid is gone (`kill -0`), or when the
# pid is alive but `ps -o lstart=` for it now differs from the start time recorded when the lock was
# taken (same pid number, different process). There is deliberately no time ceiling on a live owner —
# a lock is only ever judged by whether its owner process still is the process that took it, never by
# age, so a slow-but-live sync can hold it as long as it needs to. An owner line with no third field
# (written before this check existed) or one `ps` cannot answer right now falls back to `kill -0`
# alone, same as before — a lock is never stolen just because the extra check could not be made. With
# no owner file at all (a sync.sh from before owner files, or one killed between its mkdir and the
# write) a lock dir is stale only once older than 10 minutes. Breaking a stale lock is serialised by a
# second mkdir lock and re-checked inside it, so two runs that both saw the same dead owner can never
# both remove-and-retake it (the second would delete the first's fresh lock). A busy lock is logged as
# `skipped`, said on stderr with the holder, and exits 3 (the SessionEnd hook swallows it; `make
# claude_sync` shows it) but never writes .sync-status: the holder's pending/ok/error is newer.
LOCKDIR="$HERE/.sync.lock.d"; LOCKBRK="$HERE/.sync.lock.break"; HAVE_LOCKDIR=""; HAVE_LOCKBRK=""
OWNER_PID=""; OWNER_TS=""; OWNER_START=""; STALE_WHY=""
cleanup() {
  [ -n "$ERRF" ] && rm -f "$ERRF"
  [ -n "$HAVE_LOCKBRK" ] && rmdir "$LOCKBRK" 2>/dev/null
  [ -n "$HAVE_LOCKDIR" ] && rm -f "$LOCKDIR/owner" && rmdir "$LOCKDIR" 2>/dev/null
  return 0
}
trap cleanup EXIT
# pid_start <pid> — the process's start time as `ps -o lstart=` reports it, or empty when `ps` has
# nothing to say (no such pid, or a `ps` without `lstart`, e.g. some minimal containers): callers then
# fall back to `kill -0` alone rather than trust an empty answer either way.
pid_start() { ps -o lstart= -p "$1" 2>/dev/null | sed -e 's/^ *//' -e 's/ *$//'; }
owner_stamp() { printf '%s %s %s\n' "$$" "$(date -u +%FT%TZ)" "$(pid_start "$$")"; }
# owner_of <file> — sets OWNER_PID / OWNER_TS / OWNER_START from a `<pid> <utc-ts> <process-start-time>`
# owner file; OWNER_START is empty for an old two-field line, and for both when the file is absent or partial
owner_of() {
  OWNER_PID=""; OWNER_TS=""; OWNER_START=""
  [ -f "$1" ] || return 0
  local p="" t="" s=""
  read -r p t s <"$1" 2>/dev/null || true
  case "$p" in ''|*[!0-9]*) return 0 ;; esac
  OWNER_PID=$p; OWNER_TS=${t:-?}; OWNER_START=$s
}
# owner_gone — is the owner recorded in $OWNER_PID/$OWNER_START (set by owner_of) no longer the
# process that took the lock? True when the pid is gone outright, or when it is alive but `ps
# -o lstart=` for it now differs from the start time recorded at lock-take time (the pid was reused,
# same number, different process — see the block comment above). Sets $STALE_WHY on true. A live pid
# with no recorded start (an owner line from before this check) or one `ps` can't answer right now is
# judged by `kill -0` alone: never steal a lock just because the extra check could not be made.
owner_gone() {
  if ! kill -0 "$OWNER_PID" 2>/dev/null; then STALE_WHY="owner pid $OWNER_PID is gone"; return 0; fi
  [ -n "$OWNER_START" ] || return 1
  local now; now="$(pid_start "$OWNER_PID")"
  [ -n "$now" ] || return 1
  [ "$now" = "$OWNER_START" ] && return 1
  STALE_WHY="owner pid $OWNER_PID is a different process now (reused pid, start time changed)"
  return 0
}
# lockdir_stale — is $LOCKDIR left behind by a run that is no longer there? (sets STALE_WHY)
# $LOCKDIR itself may already be gone: the fast `mkdir` in take_lockdir can lose to a holder that
# releases the lock in the gap before this runs, and a missing dir is a free lock, not a busy one —
# treat it as stale so take_lockdir's break-and-retake path (already serialised by $LOCKBRK) just
# recreates it, instead of reporting busy on a lock nothing holds any more.
lockdir_stale() {
  [ -d "$LOCKDIR" ] || { STALE_WHY="lock dir vanished before the staleness check"; return 0; }
  owner_of "$LOCKDIR/owner"
  if [ -n "$OWNER_PID" ]; then
    owner_gone && return 0
    return 1
  fi
  [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +10 2>/dev/null)" ] || return 1
  STALE_WHY="no owner recorded, older than 10 min"; return 0
}
own_lockdir() { HAVE_LOCKDIR=1; owner_stamp >"$LOCKDIR/owner"; }
take_lockdir() {
  if mkdir "$LOCKDIR" 2>/dev/null; then own_lockdir; return 0; fi
  lockdir_stale || return 1
  mkdir "$LOCKBRK" 2>/dev/null || return 1
  HAVE_LOCKBRK=1
  local rc=1
  # re-check under the breaker: another run may have broken it and taken a fresh lock meanwhile
  if lockdir_stale; then
    rm -f "$LOCKDIR/owner"; rmdir "$LOCKDIR" 2>/dev/null
    if mkdir "$LOCKDIR" 2>/dev/null; then own_lockdir; log "lock: removed a stale lock dir ($STALE_WHY)"; rc=0; fi
  fi
  rmdir "$LOCKBRK" 2>/dev/null; HAVE_LOCKBRK=""
  return "$rc"
}
skipped() {
  local who="lock busy"
  [ -n "$OWNER_PID" ] && who="lock busy (held by pid $OWNER_PID since $OWNER_TS)"
  log "skipped: $who — $*"; printf 'sync.sh: skipped, %s — %s\n' "$who" "$*" >&2
}
take_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>>"$HERE/.sync.lock"
    if flock -w "$LOCK_WAIT" 9; then owner_stamp >"$HERE/.sync.lock"; return 0; fi
    owner_of "$HERE/.sync.lock"
    skipped "another sync.sh holds .sync.lock (hung SessionEnd sync? \`pgrep -af sync.sh\`, kill it)"
  else
    take_lockdir && return 0
    owner_of "$LOCKDIR/owner"
    local hint="\`rm -r\` it if no sync is running"
    [ -d "$LOCKBRK" ] && hint="$hint; so is $LOCKBRK, left by a run killed while breaking a stale lock"
    skipped "no flock on this host and $LOCKDIR is held ($hint)"
  fi
  return 1
}
take_lock || exit 3

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
elif [ -n "$HELD" ]; then status "held $HELD"
else status "ok ${PULLED:-kit no-origin}"; fi
exit 0
