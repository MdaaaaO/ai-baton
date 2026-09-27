#!/usr/bin/env bash
# bot-verdict.sh <owner/repo> <pr> <head> [reviews_json_file]
# Canonical bot-verdict lookup — the ONE place that reads a review bot's `### Assessment:` review,
# replacing five re-implementations (fetch-context.sh, pr-scan.sh, pr-watch.sh, pr-merge.sh,
# trivial-check.py) that had drifted apart in match semantics and capture regexes.
#
# Prints exactly one of: green | yellow | red | none
#   - green/yellow/red: the bot's latest review whose `commit_id` is a prefix-match of <head> (a full
#     40-char sha or a short prefix both work — a full sha "starts with" itself) carries that emoji on
#     its `Assessment:` line.
#   - none: the bot is configured but has not posted a matching Assessment on this head yet.
# Exit codes:
#   0  — a verdict was determined (including "none"); stdout holds it.
#   2  — `github.review_bot` (or `PR_WATCH_BOT_LOGIN`) resolves to empty: no bot configured on this
#        machine. Callers must gate on CI + human review instead — never wait on this, never read exit
#        2 as "none" (a machine with a bot and no verdict yet is a different, waitable state).
#   1  — the `gh api` call failed; stderr carries `gh`'s error. A caller must treat this as an error,
#        never as "no change" / "no verdict yet".
#
# Optional 4th arg: a file holding an already-fetched reviews array (flattened across pages, as
# fetch-context.sh's reviews.json or pr-scan.sh's per-candidate $reviews already are) — skips the
# network call for a caller that fetched the listing for its own purposes already.
set -u
repo=${1:?usage: bot-verdict.sh <owner/repo> <pr> <head_sha_or_prefix> [reviews_json_file]}
pr=${2:?pr number required}
head=${3:?head sha (or short prefix) required}
reviews_file=${4:-}
KIT="$(cd "$(dirname "$0")/../.." && pwd)"
eval "$(python3 "$KIT/context-db/bin/kit_profile.py" gh-env)"  # github.sandbox_token_prefix, if any
# `kit_profile.py get` exits 1 when the key is ABSENT (a real failure) and exits 0 printing an empty
# line when the value is merely "" — capture the exit status separately, never fold a failed lookup
# into "no bot configured" (that opens every merge/wait gate that mode skips). PR_WATCH_BOT_LOGIN, when
# set, overrides and skips the lookup entirely.
if [ -n "${PR_WATCH_BOT_LOGIN:-}" ]; then
  bot=$PR_WATCH_BOT_LOGIN
else
  boterr=$(mktemp)
  bot=$(python3 "$KIT/context-db/bin/kit_profile.py" get github.review_bot 2>"$boterr"); botrc=$?
  if [ $botrc -ne 0 ]; then
    msg=$(head -1 "$boterr"); echo "bot-verdict: kit_profile.py get github.review_bot failed: ${msg:-key absent from the env store — run kb.py config-set github.review_bot '' (or the bot login)}" >&2
    rm -f "$boterr"; exit 1
  fi
  rm -f "$boterr"
fi
[ -z "$bot" ] && exit 2

if [ -n "$reviews_file" ]; then
  reviews=$(cat "$reviews_file" 2>&1) || { echo "bot-verdict: cannot read $reviews_file" >&2; exit 1; }
else
  errf=$(mktemp)
  raw=$(gh api --paginate "repos/$repo/pulls/$pr/reviews?per_page=100" 2>"$errf"); rc=$?
  err=$(cat "$errf" 2>/dev/null); rm -f "$errf"
  if [ "$rc" -ne 0 ]; then
    echo "bot-verdict: gh api repos/$repo/pulls/$pr/reviews failed: $(printf '%s' "$err" | head -1)" >&2
    exit 1
  fi
  reviews=$(printf '%s' "$raw" | jq -sc 'add // []' 2>&1) || { echo "bot-verdict: unparsable reviews JSON for repos/$repo/pulls/$pr/reviews" >&2; exit 1; }
fi

verdict=$(printf '%s' "$reviews" | jq -r --arg bot "$bot" --arg h "$head" '
  [.[] | select(.user.login==$bot and (.commit_id|startswith($h)) and ((.body // "")|test("Assessment")))]
  | last
  | if . == null then "none"
    else ((.body // "") as $b
      | if ($b|test("Assessment:[^\\n]*🟢")) then "green"
        elif ($b|test("Assessment:[^\\n]*🟡")) then "yellow"
        elif ($b|test("Assessment:[^\\n]*🔴")) then "red"
        else "none" end)
    end' 2>&1)
rc=$?
if [ $rc -ne 0 ]; then
  echo "bot-verdict: jq failed parsing reviews for repos/$repo/pulls/$pr/reviews: $(printf '%s' "$verdict" | head -1)" >&2
  exit 1
fi
printf '%s\n' "$verdict"
