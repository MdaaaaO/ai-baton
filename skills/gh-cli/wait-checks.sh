#!/usr/bin/env bash
# wait-checks.sh — wait until every check on a PR head (or a commit) has finished. Portable: the REST
# check-runs / check-suites / status APIs work on every gh version (`gh pr checks --json` does not exist
# on older gh, e.g. 2.46). Run it as `bash wait-checks.sh …` (the execute bit is unreliable on some mounts).
#
#   bash wait-checks.sh <owner/repo> <pr-number|full-sha> [timeout-s=900] [interval-s=20]
#
# Done = at least one check, every check run completed, every check suite that has runs completed
# (covers `needs:` jobs not yet created), every legacy commit status settled, and the same finished set
# seen on two polls in a row (a workflow triggered by another one registers late; a suite still `queued`
# with 0 runs is ignored, so the second poll is what catches it). A deadline that lands between the two
# polls gets one more poll to confirm.
# Prints `name<TAB>status<TAB>conclusion` per check once done (legacy statuses as `name<TAB>status<TAB>state`).
# Exit: 0 all passed (success/neutral/skipped) · 1 at least one did not pass · 2 bad arguments or a query
# failed (reason on stderr — never read as "still pending") · 124 deadline hit (unfinished checks on stderr).
# The PR head is resolved once at start; a push during the wait is not followed — re-run on the new head.
set -u
usage() { sed -n '2,/^set -u/p' "$0" | sed '$d' >&2; exit 2; }
[ $# -ge 2 ] || usage
repo=$1 ref=$2 timeout=${3:-900} interval=${4:-20}
[[ $timeout =~ ^[0-9]+$ && $interval =~ ^[1-9][0-9]*$ ]] || { echo "wait-checks: timeout/interval must be whole seconds" >&2; usage; }
KIT="$(cd "$(dirname "$0")/../.." && pwd)"
eval "$(python3 "$KIT/context-db/bin/kit_profile.py" gh-env)"  # github.sandbox_token_prefix, if any — every other gh script evals this

if [[ $ref =~ ^[0-9]{1,9}$ ]]; then
  sha=$(gh api "repos/$repo/pulls/$ref" --jq .head.sha) || { echo "wait-checks: cannot read $repo#$ref" >&2; exit 2; }
else
  sha=$ref
fi
q() { echo "wait-checks: $1 query failed for $repo@${sha:0:7}" >&2; exit 2; }

deadline=$(( $(date +%s) + timeout )) prev=
while :; do
  # --paginate: one output line per run on every page (a per-page --jq is fine for line output)
  runs=$(gh api --paginate "repos/$repo/commits/$sha/check-runs?per_page=100" \
           --jq '.check_runs[] | "\(.name)\t\(.status)\t\(.conclusion // "-")"') || q check-runs
  # suites without runs are apps that never report (e.g. a review app's suite stays `queued` forever)
  open_suites=$(gh api --paginate "repos/$repo/commits/$sha/check-suites?per_page=100" \
           --jq '.check_suites[] | select(.latest_check_runs_count > 0 and .status != "completed") | .app.slug') || q check-suites
  # legacy statuses: pending → not done; success/failure/error → completed with that state
  stats=$(gh api --paginate "repos/$repo/commits/$sha/statuses?per_page=100" \
           --jq '.[] | "\(.context)\t\(.state)"') || q statuses
  stats=$(awk -F'\t' '$1!="" && !seen[$1]++ { print $1 "\t" ($2=="pending" ? "pending" : "completed") "\t" $2 }' <<<"$stats")
  rows=$(printf '%s\n%s\n' "$runs" "$stats" | sed '/^$/d' | sort)

  if [ -n "$rows" ] && [ -z "$open_suites" ] && ! grep -qv $'\tcompleted\t' <<<"$rows"; then
    if [ "$rows" = "$prev" ]; then
      printf '%s\n' "$rows"
      awk -F'\t' '$3!="success" && $3!="neutral" && $3!="skipped" {bad=1} END {exit bad}' <<<"$rows" && exit 0
      exit 1
    fi
    prev=$rows                                  # finished once — confirm on the next poll
  else
    prev=
  fi
  if [ "$(date +%s)" -ge "$deadline" ] && { [ -z "$prev" ] || [ -n "${grace-}" ]; }; then
    [ -n "$prev" ] && { echo "wait-checks: ${timeout}s deadline hit on $repo@${sha:0:7}; finished once, changed on the confirming poll" >&2; exit 124; }
    echo "wait-checks: ${timeout}s deadline hit on $repo@${sha:0:7}; not finished:" >&2
    [ -n "$rows" ] || echo "(no checks registered)" >&2
    grep -v $'\tcompleted\t' <<<"$rows" >&2
    [ -z "$open_suites" ] || echo "suites still running: $(echo $open_suites)" >&2
    exit 124
  fi
  [ "$(date +%s)" -ge "$deadline" ] && grace=1   # past the deadline with a finished set: one confirming poll
  sleep "$interval"
done
