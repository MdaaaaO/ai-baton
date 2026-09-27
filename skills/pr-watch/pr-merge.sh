#!/bin/bash
# pr-merge.sh <owner/repo> <pr> — run the merge gates (bot review + approval + 0 open threads) and squash-merge when they hold.
#   phase 1: wait for the review bot's Assessment on the CURRENT head (review object, via bot-verdict.sh) — skipped
#     entirely when github.review_bot is empty (no-bot mode gates on CI + human review only; one line at start says which)
#   phase 2: if BEHIND, update-branch (with expected_head_sha), force a full bot review on the merge head (draft
#     toggle) when in bot mode, wait again
#   phase 3: squash-merge once state is CLEAN, reviewDecision APPROVED, 0 unresolved threads
# Every gh listing is paginated (per_page=100 + --paginate, reviewThreads cursor-looped): a PR with >30 reviews
# or >100 threads otherwise hides the newest verdict / an open thread and the wait never ends.
# Every gh/graphql call below checks its own exit status: a failed query is an ERROR line (to stdout — the
# Monitor reads stdout), never read as "no verdict yet" / "0 open threads". Exit 0 = merge attempted.
set -u
R=$1; PR=$2
KIT="$(cd "$(dirname "$0")/../.." && pwd)"
eval "$(python3 "$KIT/context-db/bin/kit_profile.py" gh-env)"  # github.sandbox_token_prefix, if any
ERRF=$(mktemp); trap 'rm -f "$ERRF"' EXIT
err_line(){ echo "ERROR $R#$PR $(head -1 "$ERRF" 2>/dev/null | tr -d '\r')"; }
# The review bot's login comes from the env config (github.review_bot); PR_WATCH_BOT_LOGIN overrides (skips the
# lookup). `kit_profile.py get` exits 1 when the key is ABSENT (a real failure) and exits 0 printing an empty
# line when the value is merely "" — capture the exit status separately: a failed lookup is an error (this
# opens the merge gate the no-bot branch skips), never read the same as "no bot configured".
if [ -n "${PR_WATCH_BOT_LOGIN:-}" ]; then
  bot=$PR_WATCH_BOT_LOGIN
else
  bot=$(python3 "$KIT/context-db/bin/kit_profile.py" get github.review_bot 2>"$ERRF"); botrc=$?
  [ $botrc -eq 0 ] || { [ -s "$ERRF" ] || echo "key absent from the env store — run kb.py config-set github.review_bot '' (or the bot login)" >"$ERRF"; err_line; exit 1; }
fi

head_of(){ gh pr view "$PR" --repo "$R" --json headRefOid -q '.headRefOid[0:9]' 2>"$ERRF"; }
head_full(){ gh pr view "$PR" --repo "$R" --json headRefOid -q '.headRefOid' 2>"$ERRF"; }   # update-branch needs the FULL sha, not the 9-char display prefix
merge_state(){ gh pr view "$PR" --repo "$R" --json mergeStateStatus -q .mergeStateStatus 2>"$ERRF"; }
verdict_on(){ PR_WATCH_BOT_LOGIN=$bot bash "$KIT/skills/pr-watch/bot-verdict.sh" "$R" "$PR" "$1" 2>"$ERRF"; }
force_review(){ gh pr ready "$PR" --repo "$R" --undo >/dev/null 2>&1; sleep 5; gh pr ready "$PR" --repo "$R" >/dev/null 2>&1; }
# reviewThreads, cursor-paginated (a PR with >100 threads otherwise hides an open one on page 2 forever).
open_threads(){
  local o=${R%/*} r=${R#*/} cursor="" total=0 resp rc
  local q1='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100){pageInfo{hasNextPage endCursor}nodes{isResolved}}}}}'
  local q2='query($o:String!,$r:String!,$n:Int!,$c:String){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100,after:$c){pageInfo{hasNextPage endCursor}nodes{isResolved}}}}}'
  while :; do
    if [ -n "$cursor" ]; then resp=$(gh api graphql -f query="$q2" -F o="$o" -F r="$r" -F n="$PR" -F c="$cursor" 2>"$ERRF")
    else resp=$(gh api graphql -f query="$q1" -F o="$o" -F r="$r" -F n="$PR" 2>"$ERRF"); fi
    rc=$?
    if [ $rc -ne 0 ] || [ -z "$resp" ]; then return 1; fi
    total=$((total + $(jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved==false)] | length' <<<"$resp")))
    if [ "$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' <<<"$resp")" = "true" ]; then
      cursor=$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor' <<<"$resp")
    else
      break
    fi
  done
  printf '%s\n' "$total"
}
# APPROVED reviews on the current head (paginated): the gate where the repo requires no review and reviewDecision is ""
approved_on_head(){
  local full; full=$(head_full) && [ -n "$full" ] || return 1
  gh api --paginate "repos/$R/pulls/$PR/reviews?per_page=100" -q "[.[] | select(.commit_id == \"$full\" and .state == \"APPROVED\")] | length" 2>"$ERRF" \
    | awk '{ n += $1 } END { print n + 0 }'
  return "${PIPESTATUS[0]}"
}
update_branch(){ gh api -X PUT "repos/$R/pulls/$PR/update-branch" -f expected_head_sha="$1" 2>"$ERRF" >/dev/null; }

wait_verdict(){
  local h=$1 v rc
  for _ in $(seq 1 40); do
    v=$(verdict_on "$h"); rc=$?
    case $rc in
      1) err_line; return 1 ;;
      2) return 1 ;;   # bot disappeared mid-run — defensive only, we only get here when $bot was non-empty at start
    esac
    if [ "$v" != none ]; then
      echo "BOT on $h: $v"
      [ "$v" = green ] && return 0 || return 1
    fi
    cur=$(head_of); [ -n "$cur" ] || { err_line; return 1; }
    [ "$cur" = "$h" ] || { echo "HEAD MOVED to $cur while waiting on $h — rerun"; return 2; }
    sleep 60
  done
  echo "TIMEOUT: no bot verdict on $h after 40 min"; return 3
}

H=$(head_of); [ -n "$H" ] || { err_line; exit 1; }
if [ -n "$bot" ]; then echo "pr-merge: bot mode — gating on $bot Assessment + CI + human review"
else echo "pr-merge: no-bot mode (github.review_bot empty) — gating on CI + human review only"; fi
echo "phase 1: head $H"
if [ -n "$bot" ]; then wait_verdict "$H" || exit 1; fi
ot=$(open_threads); [ $? -eq 0 ] || { err_line; exit 1; }
[ "$ot" = "0" ] || { echo "open threads after verdict — resolve them, then rerun"; exit 1; }
st=$(merge_state); [ -n "$st" ] || { err_line; exit 1; }
if [ "$st" = "BEHIND" ]; then
  full=$(head_full); [ -n "$full" ] || { err_line; exit 1; }
  update_branch "$full"; [ $? -eq 0 ] || { err_line; exit 1; }
  sleep 30
  H=$(head_of); [ -n "$H" ] || { err_line; exit 1; }
  echo "phase 2: updated to $H"
  if [ -n "$bot" ]; then echo "forcing review"; force_review; wait_verdict "$H" || exit 1; fi
  ot=$(open_threads); [ $? -eq 0 ] || { err_line; exit 1; }
  [ "$ot" = "0" ] || { echo "open threads on $H — resolve, then rerun"; exit 1; }
fi
for _ in $(seq 1 60); do
  resp=$(gh pr view "$PR" --repo "$R" --json headRefOid,mergeStateStatus,reviewDecision -q '[.headRefOid[0:9],.mergeStateStatus,.reviewDecision]|join("|")' 2>"$ERRF"); rc=$?
  if [ $rc -ne 0 ] || [ -z "$resp" ]; then err_line; exit 1; fi
  # split on `|`: reviewDecision is "" where the repo requires no review (#89) — a space split lost the field and
  # `set -u` killed the script before the merge
  IFS='|' read -r cur st rd <<EOF_RESP
$resp
EOF_RESP
  [ "$cur" = "$H" ] || { echo "HEAD MOVED to $cur — rerun"; exit 1; }
  if [ -z "$rd" ]; then  # no required review: the gate is still a review — an APPROVED one on this head
    ap=$(approved_on_head); [ $? -eq 0 ] || { err_line; exit 1; }
    if [ "$ap" -gt 0 ]; then rd=APPROVED; else rd=NONE; fi
  fi
  case "$st $rd" in
    "CLEAN APPROVED") gh pr merge "$PR" --repo "$R" --squash 2>&1 | tail -1; echo "MERGE ATTEMPTED on $H"; exit 0;;
    "BLOCKED APPROVED"|"UNSTABLE APPROVED"|"UNKNOWN APPROVED") sleep 60;;   # required checks still running
    "BEHIND APPROVED") echo "BEHIND again (main moved) — rerun"; exit 1;;
    *) echo "state '$st' decision '$rd' — needs a human (approval missing or checks red; NONE = no required review and no approval on this head)"; exit 1;;
  esac
done; echo "TIMEOUT waiting for CLEAN"; exit 1
