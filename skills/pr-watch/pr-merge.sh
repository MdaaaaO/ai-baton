#!/bin/bash
# pr-merge.sh <owner/repo> <pr> — run the merge gates (bot review + approval + 0 open threads) and squash-merge when they hold.
#   phase 1: wait for the review bot's Assessment on the CURRENT head (review object, via bot-verdict.sh) — skipped
#     entirely when github.review_bot is empty (no-bot mode gates on CI + human review only; one line at start says which)
#   phase 2: if BEHIND, update-branch (with expected_head_sha), force a full bot review on the merge head by
#     removing and re-adding the bot as a requested reviewer when in bot mode, wait again
#   phase 3: squash-merge once state is CLEAN, reviewDecision APPROVED, 0 unresolved threads
# Every gh listing is paginated (per_page=100 + --paginate, reviewThreads cursor-looped): a PR with >30 reviews
# or >100 threads otherwise hides the newest verdict / an open thread and the wait never ends.
# Every gh/graphql call below checks its own exit status: a failed query is an ERROR line (to stdout — the
# Monitor reads stdout), never read as "no verdict yet" / "0 open threads".
# Exit 0 = the squash-merge call succeeded AND the PR's merged_at came back set (confirmed merged).
# Exit 1 = a gate failed, the merge call itself failed, or merged_at was still unset after a call that reported
#   success (verify by hand — never read exit 0 elsewhere as "merged", only this script's own exit 0).
# Exit 3 = gave up: CLEAN + APPROVED never held within the deadline (a bot verdict wait or the final poll loop).
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
# Remove then re-add the bot as a requested reviewer — a plain re-request is a no-op on a merge-commit head
# (GitHub thinks it already asked). This replaced flipping the PR to draft and back (`gh pr ready --undo` then
# `gh pr ready`): that toggle is visible to every other reviewer and cancels any `ready_for_review`-triggered
# run for the few seconds the PR sits in draft, for no benefit pr-merge.sh needs.
force_review(){ gh api -X DELETE "repos/$R/pulls/$PR/requested_reviewers" -f "reviewers[]=$bot" >/dev/null 2>"$ERRF"; gh api -X POST "repos/$R/pulls/$PR/requested_reviewers" -f "reviewers[]=$bot" >/dev/null 2>"$ERRF"; }
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
# The review gate where the repo requires no review and reviewDecision is "": each reviewer's LATEST decisive review
# (APPROVED / CHANGES_REQUESTED / DISMISSED; comments do not decide) — any latest CHANGES_REQUESTED blocks, and an
# APPROVED counts only on the current head. Prints APPROVED | CHANGES_REQUESTED | NONE. All pages, merged before grouping.
review_gate_on_head(){
  local full out; full=$(head_full) && [ -n "$full" ] || return 1
  out=$(gh api --paginate "repos/$R/pulls/$PR/reviews?per_page=100" 2>"$ERRF") || return 1
  jq -rs --arg h "$full" '
    add // [] | map(select(.state == "APPROVED" or .state == "CHANGES_REQUESTED" or .state == "DISMISSED"))
    | group_by(.user.login) | map(max_by(.submitted_at))
    | if any(.state == "CHANGES_REQUESTED") then "CHANGES_REQUESTED"
      elif any(.state == "APPROVED" and .commit_id == $h) then "APPROVED"
      else "NONE" end' <<<"$out"
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
if [ -n "$bot" ]; then wait_verdict "$H"; wvrc=$?; [ $wvrc -eq 0 ] || { [ $wvrc -eq 3 ] && exit 3; exit 1; }; fi
ot=$(open_threads); [ $? -eq 0 ] || { err_line; exit 1; }
[ "$ot" = "0" ] || { echo "open threads after verdict — resolve them, then rerun"; exit 1; }
st=$(merge_state); [ -n "$st" ] || { err_line; exit 1; }
if [ "$st" = "BEHIND" ]; then
  full=$(head_full); [ -n "$full" ] || { err_line; exit 1; }
  update_branch "$full"; [ $? -eq 0 ] || { err_line; exit 1; }
  sleep 30
  H=$(head_of); [ -n "$H" ] || { err_line; exit 1; }
  echo "phase 2: updated to $H"
  if [ -n "$bot" ]; then echo "forcing review"; force_review; wait_verdict "$H"; wvrc=$?; [ $wvrc -eq 0 ] || { [ $wvrc -eq 3 ] && exit 3; exit 1; }; fi
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
  if [ -z "$rd" ]; then  # no required review: the gate is still a review — each reviewer's latest, approved on this head
    rd=$(review_gate_on_head) && [ -n "$rd" ] || { err_line; exit 1; }
  fi
  case "$st $rd" in
    "CLEAN APPROVED")
      # `gh pr merge`'s own exit status, not the exit status of the `tail` that used to trail it in a pipe —
      # that piped form reported "MERGE ATTEMPTED" (and exit 0) whether or not the merge actually went through.
      merr=$(gh pr merge "$PR" --repo "$R" --squash 2>&1); mrc=$?
      if [ $mrc -ne 0 ]; then echo "MERGE FAILED on $H: $(printf '%s' "$merr" | tail -1)"; exit 1; fi
      ma=$(gh api "repos/$R/pulls/$PR" --jq '.merged_at' 2>"$ERRF")
      if [ -n "$ma" ] && [ "$ma" != "null" ]; then echo "MERGED on $H"; exit 0
      else err_line; echo "gh pr merge reported success but merged_at is still unset on $H — verify by hand"; exit 1
      fi
      ;;
    "BLOCKED APPROVED"|"UNSTABLE APPROVED"|"UNKNOWN APPROVED") sleep 60;;   # required checks still running
    "BEHIND APPROVED") echo "BEHIND again (main moved) — rerun"; exit 1;;
    *) echo "state '$st' decision '$rd' — needs a human (approval missing or checks red; NONE = no required review and no approval on this head)"; exit 1;;
  esac
done; echo "gave up: not CLEAN/APPROVED within the deadline"; exit 3
