#!/usr/bin/env bash
# pr-scan.sh [--days N] [--limit N] [--repo owner/name]... [--mark] [--quiet]
# Builds the user's review queue: direct requests > team (CODEOWNERS) requests > sweep of configured repos.
# Read-only against GitHub. Writes candidates.jsonl + queue.json under a fresh $OUT run dir (latest symlink
# next to it); with --mark appends `surfaced` rows to the ledger so the next run can say what is new.
#
# kind: new = never reviewed by us; re_review = we reviewed an older head; follow_up = same head we reviewed,
# but a thread WE opened has a reply we have not answered (last comment not ours). Our own open-but-unanswered
# threads never re-surface — the open thread is the gate, not a to-do.
# new (summary) = shown rows not yet `surfaced` on this (head, kind); NO-OP for the fork = new=0.
# errors= counts gh calls that failed after a retry (a failed call is never an empty queue); retries= the ones
# that recovered. Concurrency: one scan at a time (.scan.lock); every ledger append takes .ledger.lock.
set -uo pipefail
ROOT=${PR_REVIEW_HOME:-$(cd "$(dirname "$0")/../../.." && pwd)/.context/state/pr-review}
CFG=$ROOT/config.json; LEDGER=$ROOT/ledger.jsonl; touch "$LEDGER"
# a STABLE per-user base (survives the session and the login): the 7-day run.* retention and the `latest` symlink
# below only mean something in a directory shared across sweeps. PR_SCAN_OUT overrides.
BASE_OUT=${PR_SCAN_OUT:-$(python3 "$(dirname "$0")/../../context-db/bin/kit_profile.py" scratch --stable pr-scan)} || { echo "pr-scan: cannot resolve the output dir (kit_profile.py scratch failed)" >&2; exit 2; }
[ -n "$BASE_OUT" ] || { echo "pr-scan: empty output dir" >&2; exit 2; }; mkdir -p "$BASE_OUT"
find "$BASE_OUT" -mindepth 1 -maxdepth 1 -type d -name 'run.*' -mtime +7 -exec rm -rf {} + 2>/dev/null
OUT=$(mktemp -d "$BASE_OUT/run.$(date -u +%Y%m%dT%H%M%SZ).XXXX"); ln -sfn "$OUT" "$BASE_OUT/latest"
ME=$(jq -r .login "$CFG"); OWNER=$(jq -r .owner "$CFG")
DAYS=$(jq -r .days "$CFG"); CAP=$(jq -r .enrich_cap "$CFG"); MAXROWS=$(jq -r .max_rows "$CFG")
DEEP=$(jq -r .deep_lines "$CFG"); BOTS=$(jq -c .bots "$CFG")
AUTO_MODE=$(jq -r '.auto_approve.mode // "off"' "$CFG"); AUTO_BOTS=$(jq -c '.auto_approve.bot_authors // []' "$CFG")
AUTO_MAXF=$(jq -r '.auto_approve.max_files // 10' "$CFG"); AUTO_MAXL=$(jq -r '.auto_approve.max_lines // 200' "$CFG")
TRIVIAL=$(dirname "$0")/../pr-review/scripts/trivial-check.py
KIT=$(cd "$(dirname "$0")/../.." && pwd)
eval "$(python3 "$KIT/context-db/bin/kit_profile.py" gh-env)"  # github.sandbox_token_prefix, if any
mapfile -t REPOS < <(jq -r '.sweep_repos[]' "$CFG"); MARK=0; QUIET=0; REPO_OVERRIDE=()
while [ $# -gt 0 ]; do case $1 in
  --days) DAYS=$2; shift 2;; --limit) CAP=$2; shift 2;; --repo) REPO_OVERRIDE+=("$2"); shift 2;;
  --mark) MARK=1; shift;; --quiet) QUIET=1; shift;;
  *) echo "usage: pr-scan.sh [--days N] [--limit N] [--repo o/r]... [--mark] [--quiet]" >&2; exit 2;; esac; done
[ ${#REPO_OVERRIDE[@]} -gt 0 ] && REPOS=("${REPO_OVERRIDE[@]}")
ERR=$OUT/errors.txt; : > "$ERR"; : > "$ERR.raw"
since=$(date -u -d "-${DAYS} days" +%Y-%m-%dT%H:%M:%SZ)

# one scan at a time — a second sweep would double-mark the ledger
exec 8>"$ROOT/.scan.lock"
flock -n 8 || { echo "error: another pr-scan holds $ROOT/.scan.lock — not starting a second sweep" >&2; exit 3; }

# gh_json <label> <gh args…> — 2 attempts with backoff; result (valid JSON, non-empty) lands in $RESP.
# Runs in the current shell so the failure counters survive (never call it inside $(…)).
FAILS=0; RETRIES=0; RESP=""
gh_json(){
  local label=$1 rc i; shift; RESP=""
  for i in 1 2; do
    RESP=$(gh "$@" 2>>"$ERR.raw"); rc=$?
    if [ $rc -eq 0 ] && [ -n "$RESP" ] && jq -e . >/dev/null 2>&1 <<<"$RESP"; then [ $i -eq 2 ] && RETRIES=$((RETRIES+1)); return 0; fi
    [ $i -eq 1 ] && sleep 2
  done
  FAILS=$((FAILS+1)); echo "FAIL $label (rc=$rc): $(tail -c 160 "$ERR.raw" | tr '\n' ' ')" >> "$ERR"; RESP=""; return 1
}
in_list(){ jq -e --arg a "$1" --argjson b "$2" '$b | index($a)' >/dev/null <<<"{}"; }   # in_list <item> <json array>

# 1. direct + team requests (search API: 2 calls). Drafts and the user's own PRs never enter the queue.
: > "$OUT/direct.jsonl"; : > "$OUT/team.jsonl"
if gh_json "search direct" search prs "user-review-requested:$ME" --state=open --owner="$OWNER" --limit 100 --json repository,number,updatedAt,isDraft,author; then
  jq -c --arg me "$ME" '.[] | select(.isDraft|not) | select(.author.login!=$me) | {key: "\(.repository.nameWithOwner)#\(.number)", src: "direct", updated: .updatedAt, author: .author.login, bot: (.author.is_bot // false)}' <<<"$RESP" > "$OUT/direct.jsonl"
fi
if gh_json "search team" search prs --review-requested="$ME" --state=open --owner="$OWNER" --limit 100 --json repository,number,updatedAt,isDraft,author; then
  jq -c --arg me "$ME" '.[] | select(.isDraft|not) | select(.author.login!=$me) | {key: "\(.repository.nameWithOwner)#\(.number)", src: "team", updated: .updatedAt, author: .author.login, bot: (.author.is_bot // false)}' <<<"$RESP" > "$OUT/team.jsonl"
fi
# 2. sweep repos (1 list call each) — pre-filter here, before any per-PR call: stale, bot, own, terminal ledger head.
: > "$OUT/sweep.jsonl"; pre_stale=0; pre_bot=0; pre_done=0
if [ -s "$LEDGER" ]; then
  LEDGER_JSON=$(jq -sc . "$LEDGER" 2>"$OUT/ledger.err") || { echo "FAIL ledger parse: $LEDGER is not valid JSONL ($(head -c 200 "$OUT/ledger.err"))"; exit 4; }
else LEDGER_JSON='[]'; fi
for r in "${REPOS[@]}"; do
  gh_json "list $r" pr list --repo "$r" --state open --draft=false --limit 100 --json number,updatedAt,isDraft,author,headRefOid || continue
  rows=$(jq -c --arg r "$r" --arg me "$ME" --arg since "$since" --argjson bots "$BOTS" --argjson led "$LEDGER_JSON" '
    .[] | select(.author.login!=$me)
    | . as $p | .author.login as $a | ((.author.is_bot // false) or (($bots|index($a))!=null)) as $isbot
    | ($led | map(select(.repo==$r and .pr==$p.number and .head==$p.headRefOid and (.status|IN("skipped","shadow_approve","auto_approved")))) | length) as $term
    | {key: "\($r)#\(.number)", src: "sweep", updated: .updatedAt, author: $a, bot: $isbot,
       drop: (if $isbot then "bot" elif .updatedAt < $since then "stale" elif $term>0 then "done" else "" end)}' <<<"$RESP") || { FAILS=$((FAILS+1)); echo "FAIL prefilter $r: jq error on pr list output" >> "$ERR"; continue; }
  pre_bot=$((pre_bot + $(grep -c '"drop":"bot"' <<<"$rows" || true)))
  pre_stale=$((pre_stale + $(grep -c '"drop":"stale"' <<<"$rows" || true)))
  pre_done=$((pre_done + $(grep -c '"drop":"done"' <<<"$rows" || true)))
  jq -c 'select(.drop=="") | del(.drop)' <<<"$rows" >> "$OUT/sweep.jsonl"
done
# union, best source wins (direct > team > sweep); enrich in priority order so the cap never starves a direct request
cat "$OUT/direct.jsonl" "$OUT/team.jsonl" "$OUT/sweep.jsonl" | jq -sc '
  group_by(.key) | map((.[0]) + {src: ([.[].src] | if index("direct") then "direct" elif index("team") then "team" else "sweep" end)})
  | sort_by([(if .src=="direct" then 0 elif .src=="team" then 1 else 2 end), (.updated|explode|map(-.))]) | .[]' > "$OUT/union.jsonl"
total=$(wc -l < "$OUT/union.jsonl")

# 3. enrich (3 calls per candidate: pull, reviews, threads) — the cap counts surviving candidates only
: > "$OUT/candidates.jsonl"; n=0; dropped_bot=$pre_bot; dropped_stale=$pre_stale; dropped_draft=0; dropped_done=$pre_done; dropped_skip=0; dropped_approved=0; dropped_cap=0; dropped_fail=0; degraded=0
threads_q='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100){nodes{isResolved opener:comments(first:1){nodes{author{login}}} last:comments(last:1){nodes{author{login}}}}}}}}'
while IFS= read -r row; do
  key=$(jq -r .key <<<"$row"); src=$(jq -r .src <<<"$row"); repo=${key%#*}; num=${key##*#}
  [ "$n" -ge "$CAP" ] && { dropped_cap=$((dropped_cap+1)); continue; }
  gh_json "pull $key" api "repos/$repo/pulls/$num" || { dropped_fail=$((dropped_fail+1)); continue; }
  pr=$RESP
  author=$(jq -r .user.login <<<"$pr"); draft=$(jq -r .draft <<<"$pr"); upd=$(jq -r .updated_at <<<"$pr"); state=$(jq -r .state <<<"$pr")
  [ "$author" = "$ME" ] && continue
  [ "$state" = "open" ] || { dropped_done=$((dropped_done+1)); continue; }
  [ "$draft" = "true" ] && { dropped_draft=$((dropped_draft+1)); continue; }
  # freshness applies to swept PRs only — a direct or team request is a request, however old the PR is
  [ "$src" = sweep ] && [[ "$upd" < "$since" ]] && { dropped_stale=$((dropped_stale+1)); continue; }
  # trivial-PR auto-approve gate (owner decision 2026-09-19): cheap pre-filter on size, then trivial-check.py (deterministic).
  # Bot authors listed in auto_approve.bot_authors (dependabot/renovate) are kept ONLY when eligible; other bots drop.
  # Auto-approve is scoped to PRs the user was asked to review (direct or via owner_teams) — sweep rows never run the gate.
  auto='null'
  if [ "$AUTO_MODE" != off ] && [ "$src" != sweep ] && [ "$(jq -r .changed_files <<<"$pr")" -le "$AUTO_MAXF" ] && [ "$(jq -r '.additions+.deletions' <<<"$pr")" -le $((AUTO_MAXL*4)) ]; then
    auto_raw=$(python3 "$TRIVIAL" "$repo" "$num" 2>>"$ERR.raw" || true)   # exit 1 = gh failure; it still prints a JSON with reasons
    auto=$(jq -c '{eligible: (.eligible // false), class, reasons: ((.reasons // [])[:3]), packages: [.packages[]? | "\(.name) \(.from)→\(.to)"], error: (.error // false)}' <<<"$auto_raw" 2>/dev/null) \
      || { auto='null'; FAILS=$((FAILS+1)); echo "FAIL trivial-check $key: unparseable output: $(head -c 160 <<<"$auto_raw" | tr '\n' ' ')" >> "$ERR"; }
    [ "$(jq -r '.error // false' <<<"$auto")" = true ] && { FAILS=$((FAILS+1)); echo "FAIL trivial-check $key: $(jq -r '.reasons[0] // ""' <<<"$auto")" >> "$ERR"; }
  fi
  if in_list "$author" "$BOTS"; then
    if in_list "$author" "$AUTO_BOTS" && [ "$(jq -r '.eligible // false' <<<"$auto")" = true ]; then :; else dropped_bot=$((dropped_bot+1)); continue; fi
  fi
  head=$(jq -r .head.sha <<<"$pr")
  # reviews: a failed fetch degrades the row (humans unknown, never dropped as done/approved) instead of hiding the PR
  reviews='[]'; deg='[]'
  if gh_json "reviews $key" api --paginate "repos/$repo/pulls/$num/reviews?per_page=100"; then reviews=$(jq -sc 'add // []' <<<"$RESP"); else deg='["reviews"]'; degraded=$((degraded+1)); fi
  mine=$(jq -c --arg me "$ME" '[.[] | select(.user.login==$me and .state!="PENDING")] | last // null' <<<"$reviews")
  my_head=$(jq -r 'if .==null then "" else ((((.body // "") | [capture("pr-review:v1 head=(?<h>[0-9a-f]+)")] | first | .h) // .commit_id)) end' <<<"$mine")
  humans=$(jq -c --argjson b "$BOTS" --arg me "$ME" '[.[] | select((.user.login as $u | ($b|index($u))|not) and .user.login!=$me and (.state=="APPROVED" or .state=="CHANGES_REQUESTED" or (.state=="COMMENTED" and ((.body//"")|length)>0)))] | group_by(.user.login) | map({login: .[0].user.login, state: (last.state)})' <<<"$reviews")
  approved=$(jq -r '[.[] | select(.state=="APPROVED")] | length' <<<"$humans")
  changes=$(jq -r '[.[] | select(.state=="CHANGES_REQUESTED")] | length' <<<"$humans")
  o=${repo%/*}; r=${repo#*/}
  threads='{"open":null,"mine":null}'
  if gh_json "threads $key" api graphql -f query="$threads_q" -F o="$o" -F r="$r" -F n="$num"; then
    threads=$(jq -c --arg me "$ME" '[.data.repository.pullRequest.reviewThreads.nodes[]? | select(.isResolved|not)] | {open: length, mine: ([.[] | select(.opener.nodes[0].author.login==$me and .last.nodes[0].author.login!=$me)] | length)}' <<<"$RESP")
  else deg=$(jq -c '. + ["threads"]' <<<"$deg"); fi
  # ledger: every status ever recorded for THIS head (order-independent — no grep on serialised key order)
  lset=$(jq -c --arg repo "$repo" --argjson pr "$num" --arg h "$head" '[.[] | select(.repo==$repo and .pr==$pr and .head==$h)]' <<<"$LEDGER_JSON")
  has(){ jq -e --arg s "$1" 'map(.status) | index($s)' >/dev/null <<<"$lset"; }
  lstatus=$(jq -r 'map(.status) | (if index("reviewed") then "reviewed" elif index("replied") then "replied" elif index("skipped") then "skipped" elif index("auto_approved") then "auto_approved" elif index("shadow_approve") then "shadow_approve" elif index("shadow_fallback") then "shadow_fallback" elif index("surfaced") then "surfaced" else "" end)' <<<"$lset")
  if [ "$my_head" = "$head" ] || has reviewed; then
    if [ "$(jq -r .mine <<<"$threads")" != "0" ] && [ "$(jq -r .mine <<<"$threads")" != "null" ]; then kind=follow_up; else dropped_done=$((dropped_done+1)); continue; fi
  elif [ -n "$my_head" ]; then kind=re_review
  elif has skipped; then dropped_skip=$((dropped_skip+1)); continue
  elif has shadow_approve || has auto_approved; then dropped_done=$((dropped_done+1)); continue   # shadow_fallback stays: it is an ordinary row
  else kind=new; fi
  if [ "$deg" = '[]' ] && [ "$approved" -gt 0 ] && [ "$src" != "direct" ] && [ "$kind" = "new" ]; then dropped_approved=$((dropped_approved+1)); continue; fi
  surfaced=$(jq -r --arg k "$kind" '[.[] | select(.status=="surfaced" and .kind==$k)] | length > 0' <<<"$lset")
  # bot verdict: one helper, shared with fetch-context/pr-watch/pr-merge/trivial-check — pass the
  # reviews we already fetched for this candidate, so this costs no extra gh call.
  bot=""
  rf=$(mktemp); printf '%s' "$reviews" > "$rf"
  bot_err=$(mktemp)
  bot_out=$(bash "$KIT/skills/pr-watch/bot-verdict.sh" "$repo" "$num" "$head" "$rf" 2>"$bot_err"); bot_rc=$?
  rm -f "$rf"
  case $bot_rc in
    0) bot=$bot_out ;;
    2) bot="" ;;                                                     # no bot configured on this machine
    *) FAILS=$((FAILS+1)); echo "FAIL bot-verdict $key: $(head -c 160 "$bot_err" | tr '\n' ' ')" >> "$ERR" ;;
  esac
  rm -f "$bot_err"
  n=$((n+1))
  jq -nc --arg repo "$repo" --argjson pr "$num" --arg head "$head" --arg src "$src" --arg kind "$kind" --arg author "$author" \
    --arg title "$(jq -r .title <<<"$pr")" --arg upd "$upd" --arg base "$(jq -r .base.ref <<<"$pr")" \
    --argjson add "$(jq .additions <<<"$pr")" --argjson del "$(jq .deletions <<<"$pr")" --argjson files "$(jq .changed_files <<<"$pr")" \
    --argjson humans "$humans" --argjson approved "$approved" --argjson changes "$changes" --argjson threads "$threads" \
    --arg bot "$bot" --arg lstatus "$lstatus" --argjson surfaced "$surfaced" --argjson deg "$deg" --argjson deep "$DEEP" --arg url "$(jq -r .html_url <<<"$pr")" --argjson auto "$auto" \
    --argjson req "$(jq -c '[.requested_reviewers[].login] + [.requested_teams[].slug | "@"+.]' <<<"$pr")" \
    '{repo:$repo, pr:$pr, head:$head, src:$src, kind:$kind, author:$author, title:$title, updated:$upd, base:$base,
      lines:($add+$del), files:$files, humans:$humans, approved:$approved, changes_requested:$changes, threads:$threads,
      bot:$bot, ledger:$lstatus, surfaced:$surfaced, degraded:$deg, deep:(($add+$del)>$deep), url:$url, requested:$req, auto:$auto,
      prio:(if $src=="direct" then 1 elif $kind!="new" then 2 elif $src=="team" and ($humans|length)==0 then 3 elif ($humans|length)==0 then 4 else 5 end)}' >> "$OUT/candidates.jsonl"
done < "$OUT/union.jsonl"

# 4. rank + present — `new` and `auto` are counted over the SHOWN rows, which are exactly the rows --mark records
jq -s 'sort_by([.prio, (.updated|explode|map(-.))])' "$OUT/candidates.jsonl" > "$OUT/queue.json"
shown=$(jq --argjson m "$MAXROWS" '.[:$m] | length' "$OUT/queue.json"); cand=$(jq length "$OUT/queue.json")
newc=$(jq --argjson m "$MAXROWS" '[.[:$m][] | select(.surfaced|not)] | length' "$OUT/queue.json")
autoc=$(jq --argjson m "$MAXROWS" '[.[:$m][] | select(.auto.eligible // false)] | length' "$OUT/queue.json")
if [ "$QUIET" = 0 ]; then
  printf '%-4s %-28s %-10s %-4s %-18s %-9s %-7s %-14s %s\n' PRIO PR SRC KIND AUTHOR LINES/F HUMANS THREADS/BOT TITLE
  jq -r --argjson m "$MAXROWS" '.[:$m][]
      | (.repo|split("/")[1]) as $r | (if .surfaced then "" else " *" end) as $star
      | (if .deep then "!" elif (.auto.eligible // false) then "A" else "" end) as $bang
      | (if (.degraded|index("reviews")) then "?" else (.humans|map(.state[:3])|join(",")) end) as $h
      | ((.threads.open // "?")|tostring) as $t | ({green:"🟢",yellow:"🟡",red:"🔴"}[.bot] // "-") as $b
      | [(.prio|tostring), ($r+"#"+(.pr|tostring)+$star), .src, .kind[:4], .author[:18], ((.lines|tostring)+"/"+(.files|tostring)+$bang), (if $h=="" then "-" else $h end), ($t+"/"+$b), .title[:60]] | @tsv' "$OUT/queue.json" \
    | awk -F'\t' '{printf "%-4s %-28s %-10s %-4s %-18s %-9s %-7s %-14s %s\n",$1,$2,$3,$4,$5,$6,$7,$8,$9}'
  echo "-- * = not surfaced before on this head; ! = over deep threshold ($DEEP lines); A = trivial-PR auto-approve eligible (mode=$AUTO_MODE); ? = review state unavailable (see errors); LINES/F = added+deleted / files; HUMANS = last state per human reviewer"
fi
echo "summary: union=$total candidates=$cand shown=$shown new=$newc auto=$autoc auto_mode=$AUTO_MODE dropped(bots=$dropped_bot draft=$dropped_draft stale>${DAYS}d=$dropped_stale done=$dropped_done skipped=$dropped_skip approved=$dropped_approved cap=$dropped_cap failed=$dropped_fail) degraded=$degraded errors=$FAILS retries=$RETRIES out=$OUT"
if [ "$MARK" = 1 ] && [ "$newc" -gt 0 ]; then
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  ( flock 9; jq -c --arg ts "$ts" --argjson m "$MAXROWS" '.[:$m][] | select(.surfaced|not) | {repo, pr, head, status:"surfaced", ts:$ts, src, kind, auto:(.auto.eligible // false)}' "$OUT/queue.json" >> "$LEDGER" ) 9>"$ROOT/.ledger.lock"
fi
[ "$FAILS" -eq 0 ]
