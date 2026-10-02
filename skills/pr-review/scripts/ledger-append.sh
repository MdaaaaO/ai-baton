#!/usr/bin/env bash
# ledger-append.sh <ledger-path> [--lock <lock-path>]
# Appends one or more rows to the shared `.context/state/pr-review/ledger.jsonl` under a lock — the
# single writer every caller shares, so a model walked through "append a ledger row" (`reference/runner.md`
# § `--auto` / `--unattended`) or a script that marks rows (`pr-scan.sh --mark-only --run <dir>`) never
# hand-rolls its own `flock`/`jq` append again. `submit-review.sh` and `reply-threads.sh` still append
# their own ledger rows inline (their own `flock`/`jq`, not this script) — not callers of this one.
#
# Rows come from stdin: one or more lines, each a single-line, compact JSON object (JSONL) — exactly the
# shape every row in the ledger already has (`{"repo","pr","head","status","ts",...}`; this script does
# not validate the keys, only that each line parses as one JSON object, so it stays usable for whatever
# shape a future status needs). Blank trailing lines are ignored; a genuinely empty stdin (no rows to
# append) is a no-op, exit 0 — a caller with nothing new to mark should not need to skip calling this.
#
# Locking: `--lock <path>` (default: `.ledger.lock` next to <ledger-path>, the name every caller already
# uses) via `with_lock` (skills/_lib/portable.sh — flock where the host has it, the portable mkdir-based
# lock dir otherwise). `with_lock` itself is non-blocking; this script polls it for up to
# LEDGER_APPEND_TIMEOUT seconds (default 10, whole seconds only) so a writer racing another one retries
# instead of failing on the first busy tick — the lock is typically held only for the length of one
# append. All validated rows are written in ONE `>>` under the held lock, so a concurrent appender can
# never interleave into the middle of this call's rows.
#
# Exit codes (distinct, never a bare non-zero — a caller branches on these, per pr-review/reference/runner.md):
#   0  appended (or nothing on stdin to append)
#   1  a stdin line is not a single valid JSON object — bad input; NOTHING is written (all rows in the
#      same call succeed or none do), the first bad line's number and content (truncated) on stderr
#   2  usage error: missing <ledger-path>, unknown flag, or --lock with no value
#   3  could not acquire the lock within LEDGER_APPEND_TIMEOUT — another writer held it throughout;
#      safe to retry the whole call, nothing was written
#   4  could not write <ledger-path> (its parent directory is missing, or the `>>` itself failed —
#      permissions, disk full); the lock IS released, nothing was written
set -uo pipefail
KIT=$(cd "$(dirname "$0")/../../.." && pwd)
# shellcheck source=../../_lib/portable.sh
. "$KIT/skills/_lib/portable.sh"  # with_lock — GNU/Linux and macOS/BSD alike

usage() { echo "usage: ledger-append.sh <ledger-path> [--lock <lock-path>]  (rows as JSONL on stdin)" >&2; exit 2; }

[ $# -ge 1 ] || usage
LEDGER=$1; shift
LOCK="$(dirname "$LEDGER")/.ledger.lock"
while [ $# -gt 0 ]; do case $1 in
  --lock) [ $# -ge 2 ] || usage; LOCK=$2; shift 2;;
  *) usage;;
esac; done
[ -n "$LEDGER" ] || usage

# read stdin once, drop blank lines (a trailing newline from `printf '%s\n'` is not a row)
rows=$(cat); rows=$(printf '%s\n' "$rows" | sed '/^[[:space:]]*$/d')
[ -n "$rows" ] || exit 0   # nothing to append — not an error

n=0
while IFS= read -r line; do
  n=$((n+1))
  if ! jq -e 'type=="object"' >/dev/null 2>&1 <<<"$line"; then
    echo "ledger-append: stdin line $n is not a single JSON object: $(printf '%s' "$line" | head -c 160)" >&2
    exit 1
  fi
done <<<"$rows"

timeout=${LEDGER_APPEND_TIMEOUT:-10}
start=$(date +%s)
acquired=""
while :; do
  if with_lock "$LOCK"; then acquired=1; break; fi
  [ $(( $(date +%s) - start )) -ge "$timeout" ] && break
  sleep 1
done
[ -n "$acquired" ] || { echo "ledger-append: could not acquire $LOCK within ${timeout}s — another writer held it" >&2; exit 3; }

werr=$(mktemp 2>/dev/null) || werr=""
if ! printf '%s\n' "$rows" >> "$LEDGER" 2>"${werr:-/dev/null}"; then
  echo "ledger-append: could not write $LEDGER: $([ -n "$werr" ] && cat "$werr")" >&2
  [ -n "$werr" ] && rm -f "$werr"
  exit 4
fi
[ -n "$werr" ] && rm -f "$werr"
exit 0
