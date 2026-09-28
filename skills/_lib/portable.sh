# shellcheck shell=dash  # sourced by both /bin/sh (sync.sh) and bash scripts — POSIX only, no bashisms
# skills/_lib/portable.sh — POSIX-shell helpers so a kit script behaves the same on GNU/Linux and
# on macOS/BSD (bash 3.2, BSD `date`/`sed`/`awk`, no `flock` or `setsid` on PATH). Not a skill — no
# SKILL.md, nothing here is invoked directly — source it: `. "$KIT/skills/_lib/portable.sh"`.
#
#   iso_to_epoch <ISO-8601 timestamp>   -> epoch seconds on stdout, exit 1 (nothing printed) if this
#                                          host's `date` cannot parse it
#   epoch_to_iso <epoch seconds>        -> ISO-8601 UTC (`%Y-%m-%dT%H:%M:%SZ`) on stdout
#   with_lock <lock-path>               -> 0 = locked (proceed), 1 = another instance holds it;
#                                          `flock` (fd 9) where it exists, else an atomic `mkdir`
#                                          released by EXIT/INT/TERM traps this call installs (INT/TERM
#                                          also `exit`, so the signal still ends the process) — a
#                                          caller that needs its own traps too must compose them after
#                                          calling with_lock, not before
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

# with_lock <path> — non-blocking. On a host with `flock`, opens <path> on fd 9 and flocks it (held
# for the life of this process, released when it exits — no trap needed there). Without `flock`
# (macOS without coreutils), takes an atomic `mkdir "<path>.d"` instead and installs an EXIT/INT/TERM
# trap to remove it, so a normal exit or a signal never leaves the next run permanently locked out.
with_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$1"
    flock -n 9
    return $?
  fi
  _portable_lockdir="$1.d"
  mkdir "$_portable_lockdir" 2>/dev/null || return 1
  # EXIT alone releases it on a normal return; INT/TERM must also `exit` (a trap that only cleans up
  # and returns does NOT end the script — the caller would keep running, unkillable by ^C/kill, with
  # its lock already gone under it) so the signal still ends the process the way its default did.
  trap 'rm -rf "$_portable_lockdir"' EXIT
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
