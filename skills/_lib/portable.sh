# shellcheck shell=dash  # sourced by pr-scan.sh, pr-watch.sh, heartbeat.sh (all bash) — POSIX only, no bashisms
# skills/_lib/portable.sh — POSIX-shell helpers so a kit script behaves the same on GNU/Linux and
# on macOS/BSD (bash 3.2, BSD `date`/`sed`/`awk`, no `flock` or `setsid` on PATH). Not a skill — no
# SKILL.md, nothing here is invoked directly — source it: `. "$KIT/skills/_lib/portable.sh"`.
#
#   iso_to_epoch <ISO-8601 timestamp>   -> epoch seconds on stdout, exit 1 (nothing printed) if this
#                                          host's `date` cannot parse it
#   epoch_to_iso <epoch seconds>        -> ISO-8601 UTC (`%Y-%m-%dT%H:%M:%SZ`) on stdout
#   with_lock <lock-path>               -> 0 = locked (proceed), 1 = another instance holds it;
#                                          `flock` (fd 9) where it exists, else a lock dir whose
#                                          `owner` file claim_owner (below) creates exclusively and
#                                          stamps with the owner's pid+host (sync.sh's own approach:
#                                          a dead owner's lock is reclaimed, a live one's never is)
#                                          and released by EXIT/INT/TERM traps this call installs
#                                          (INT/TERM also `exit`, so the signal still ends the
#                                          process) — a caller that needs its own traps too must
#                                          compose them after calling with_lock, not before
#   claim_owner <path>                  -> 0 = this call created <path> (now the holder), 1 =
#                                          it already existed — the exclusive-create primitive
#                                          with_lock (and sync.sh's own take_lockdir) claims a lock
#                                          dir's ownership with, never a plain `mkdir`'s exit status
#   detach <log-file> <cmd> [args…]     -> run <cmd> in the background, detached from this shell's
#                                          process group and controlling terminal (so a supervisor
#                                          that kills "the process group" after a timeout does not
#                                          take it down too), with stdout+stderr appended to <log-file>

# iso_to_epoch <ts> — GNU `date -d` parses ISO 8601 directly; BSD/macOS `date` has no `-d` but reads
# the format with `-j -f`, so the trailing `Z`/offset is trimmed and fed to the fixed second-precision
# form every wire date here uses (GitHub's `commit.committer.date`, `git log` ISO dates).
iso_to_epoch() {
  date -u -d "$1" +%s 2>/dev/null && return 0
  date -j -u -f "%Y-%m-%dT%H:%M:%S" "${1%Z}" +%s 2>/dev/null
}

# epoch_to_iso <epoch> — GNU `date -d @<epoch>`, else BSD `date -r <epoch>`.
epoch_to_iso() {
  date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null && return 0
  date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null
}

# claim_owner <path> — the actual holder of a lock dir is whoever creates <path> (its `owner` file)
# with the shell's own exclusive create, `(set -C; : >"<path>")` in a subshell so this call's own
# `noclobber` setting never leaks into the caller either way. POSIX `noclobber` refuses to open an
# existing file for output and is exclusive on every shell/host this kit targets (dash, bash, BSD
# sh) — unlike a plain `mkdir "<newdir>"`, whose exit status is NOT a safe exclusivity check on
# every host (some uutils coreutils builds let two concurrent callers on the same new path both exit
# 0). Returns 0 only for the caller that created the file (now the holder, free to fill it in with
# its own stamp); 1 when it already existed — a live holder's, or merely left behind by one.
claim_owner() {
  (set -C; : >"$1") 2>/dev/null
}

# with_lock <path> — non-blocking. On a host with `flock`, opens <path> on fd 9 and flocks it (held
# for the life of this process, released when it exits — no trap needed there). Without `flock`
# (macOS without coreutils), takes a lock dir `<path>.d` instead: `mkdir` creates it (a success alone
# decides nothing — see claim_owner above; a failure means the dir is someone else's, live or left
# behind) and `claim_owner` on `<path>.d/owner` is the actual exclusivity gate, so the one caller that
# wins then stamps `<pid> <host>` into it — sync.sh's own `take_lockdir` shape. A lock dir already there is stale, and reclaimed, only when its owner pid
# was recorded on THIS host (never guessed at across hosts sharing the path over a network mount) and
# `kill -0` says it is gone; a live owner's lock is never touched. Reclaiming is itself serialised by
# a second claim (`<path>.d.break/owner`), re-checking the owner under it, so two callers that both
# saw the same dead owner can never both remove-and-retake it (the second would delete the first's
# fresh lock) — same race sync.sh closes the same way. "with_lock: removed a stale lock …" goes to
# stderr when this happens. The winning lock (fresh or reclaimed) is released by EXIT/INT/TERM traps
# this call installs (INT/TERM also `exit`, so the signal still ends the process — a trap that only
# cleans up and returns would leave the caller running, unkillable by ^C/kill, with its lock already
# gone under it); release removes the owner file, then the lock dir itself.
with_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$1"
    flock -n 9
    return $?
  fi
  _portable_lockdir="$1.d"
  if mkdir "$_portable_lockdir" 2>/dev/null && claim_owner "$_portable_lockdir/owner"; then
    _portable_own=1
  else
    _portable_own=""
    _portable_owner_line=$(cat "$_portable_lockdir/owner" 2>/dev/null)
    _portable_opid=${_portable_owner_line%% *}
    _portable_ohost=${_portable_owner_line#* }
    case "$_portable_opid" in '' | *[!0-9]*) _portable_opid="" ;; esac
    if [ -n "$_portable_opid" ] && [ "$_portable_ohost" = "$(hostname 2>/dev/null)" ] \
       && ! kill -0 "$_portable_opid" 2>/dev/null; then
      # a dead owner on this host: reclaim it, but only after a second claim serialises the
      # break-and-retake and a re-check under it still sees the SAME dead owner (not a fresh
      # one another caller already took in the gap since the check above)
      mkdir "$_portable_lockdir.break" 2>/dev/null
      if claim_owner "$_portable_lockdir.break/owner"; then
        if [ "$(cat "$_portable_lockdir/owner" 2>/dev/null)" = "$_portable_owner_line" ]; then
          rm -rf "$_portable_lockdir"
          mkdir "$_portable_lockdir" 2>/dev/null
          if claim_owner "$_portable_lockdir/owner"; then
            _portable_own=1
            echo "with_lock: removed a stale lock ($_portable_lockdir, owner pid $_portable_opid is gone)" >&2
          fi
        fi
        rm -f "$_portable_lockdir.break/owner"; rmdir "$_portable_lockdir.break" 2>/dev/null
      fi
    fi
  fi
  [ -n "$_portable_own" ] || return 1
  printf '%s %s\n' "$$" "$(hostname 2>/dev/null)" > "$_portable_lockdir/owner"
  # EXIT alone releases it on a normal return; INT/TERM must also `exit` (see the block comment above).
  trap 'rm -f "$_portable_lockdir/owner"; rmdir "$_portable_lockdir" 2>/dev/null' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  return 0
}

# detach <log> <cmd> [args…] — `setsid` (util-linux) starts <cmd> in a new session, out of this
# shell's process group; macOS/BSD ship no `setsid`, so the fallback re-execs through python3's
# stdlib `os.setsid()` first (python3 is already a kit dependency — kit_profile.py, signq.py) and
# then the real command, giving the same detachment without a new binary dependency.
detach() {
  _portable_log=$1; shift
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup "$@" >>"$_portable_log" 2>&1 </dev/null &
  else
    nohup python3 -c 'import os, sys
os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])' "$@" >>"$_portable_log" 2>&1 </dev/null &
  fi
}
