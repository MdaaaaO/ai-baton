#!/usr/bin/env bash
# pr-scan.sh [--days N] [--limit N] [--repo owner/name]... [--quiet]
# pr-scan.sh --mark-only --run <dir>
# --limit N caps how many candidates get enriched (the 3 per-candidate gh calls below), not how many
# rows are shown or counted new — `max_rows` in config.json trims the table itself.
# Builds the user's review queue: direct requests > team (CODEOWNERS) requests > sweep of configured repos.
# Read-only against GitHub AND against the ledger — the sweep writes only candidates.jsonl + queue.json
# under a fresh $OUT run dir (latest symlink next to it); it never appends to the ledger itself
# (`skills/pr-scan/SKILL.md` documents the fork this runs in as read-only, so the sweep path must not write).
#
# `--mark-only --run <dir>` is the separate, non-sweeping step that does the one write this skill causes: it
# takes no `gh` call, reads the NAMED run's `queue.json` (read-only) and appends `surfaced` ledger rows for
# the shown rows not surfaced yet, via `ledger-append.sh`. `--run` is required — no `latest`-symlink fallback:
# a sweep started after a brief was shown but before this runs must never mark rows the user never saw. The
# dir must resolve inside $BASE_OUT (a path escaping it is refused) and hold a finished `queue.json` (exit 5
# otherwise, same as a dir that does not exist). The main session runs this itself, via the self-executing
# `MARK` trailer the fork's own answer ends with when it is not NO-OP — never inside the fork
# (`skills/pr-scan/SKILL.md` § Answer carries the trailer's exact shape). A run dir marks at most once: a
# `.marked` stamp beside `queue.json` makes a second `--mark-only --run` on the same run a safe no-op, not a
# double append.
#
# kind: new = never reviewed by us; re_review = we reviewed an older head; follow_up = same head we reviewed,
# but a thread WE opened has a reply we have not answered (last comment not ours). Our own open-but-unanswered
# threads never re-surface — the open thread is the gate, not a to-do. done = our review sits on this head and
# nothing waits on us: kept (lowest prio, never counted as new) while our APPROVE / REQUEST_CHANGES stands on an
# open PR, and once for a review the kit posted (ledger `reviewed` / `auto_commented`); everything else drops.
# new (summary) = shown rows not yet `surfaced` on this (head, kind), `done` rows aside; NO-OP for the fork = new=0.
# errors= counts gh calls that failed after a retry (a failed call is never an empty queue); retries= the ones
# that recovered. Concurrency: one scan at a time (.scan.lock); `--mark-only`'s ledger append takes its own
# lock (`ledger-append.sh` — `.ledger.lock`), never `.scan.lock`: it only reads candidates.jsonl/queue.json.
set -uo pipefail
KIT=$(cd "$(dirname "$0")/../.." && pwd)
# shellcheck source=../_lib/portable.sh
. "$KIT/skills/_lib/portable.sh"  # epoch_to_iso, with_lock, claim_owner — GNU/Linux and macOS/BSD alike
LEDGER_APPEND=$(dirname "$0")/../pr-review/scripts/ledger-append.sh
# the workspace's .context/ (#74) the way every kit script finds it (kit_profile.py context), never from this
# script's location: on a plugin install that is Claude Code's plugin cache, wiped on update. PR_REVIEW_HOME overrides.
CTX=$(python3 "$KIT/context-db/bin/kit_profile.py" context)
[ -n "${PR_REVIEW_HOME:-}" ] || [ -d "$CTX" ] || { echo "pr-scan: no workspace .context/ found ($CTX) — run from the workspace, or set PR_REVIEW_HOME" >&2; exit 2; }
export PR_REVIEW_HOME=${PR_REVIEW_HOME:-$CTX/state/pr-review}; ROOT=$PR_REVIEW_HOME
CFG=$ROOT/config.json; LEDGER=$ROOT/ledger.jsonl; touch "$LEDGER"
# a STABLE per-user base (survives the session and the login): the 7-day run.* retention and the `latest` symlink
# below only mean something in a directory shared across sweeps. PR_SCAN_OUT overrides.
BASE_OUT=${PR_SCAN_OUT:-$(python3 "$(dirname "$0")/../../context-db/bin/kit_profile.py" scratch --stable pr-scan)} || { echo "pr-scan: cannot resolve the output dir (kit_profile.py scratch failed)" >&2; exit 2; }
[ -n "$BASE_OUT" ] || { echo "pr-scan: empty output dir" >&2; exit 2; }; mkdir -p "$BASE_OUT"
ME=$(jq -r .login "$CFG"); OWNER=$(jq -r .owner "$CFG")
DAYS=$(jq -r .days "$CFG"); CAP=$(jq -r .enrich_cap "$CFG"); MAXROWS=$(jq -r .max_rows "$CFG")
DEEP=$(jq -r .deep_lines "$CFG"); BOTS=$(jq -c .bots "$CFG")
AUTO_MODE=$(jq -r '.auto_approve.mode // "off"' "$CFG"); AUTO_BOTS=$(jq -c '.auto_approve.bot_authors // []' "$CFG")
AUTO_MAXF=$(jq -r '.auto_approve.max_files // 10' "$CFG"); AUTO_MAXL=$(jq -r '.auto_approve.max_lines // 200' "$CFG")
# unattended auto-COMMENT gate for direct review requests (opt-in, default off) — no gh calls, computed
# straight from the row's own prio/kind/author, same as the trivial-approve pre-filter above. A `re_review`
# row (we already reviewed an older head, now re-requested) is excluded by default — a re-request must not
# ride the AUTO-COMMENT trailer on the strength of the prior review's prio alone; `include_re_review` opts
# a direct re-request back in explicitly.
AUTOC_MODE=$(jq -r '.auto_comment.mode // "off"' "$CFG"); AUTOC_PRIOS=$(jq -c '.auto_comment.prios // [1]' "$CFG")
AUTOC_MAX=$(jq -r '.auto_comment.max_per_tick // 3' "$CFG")
AUTOC_REREVIEW=$(jq -r '.auto_comment.include_re_review // false' "$CFG")
TRIVIAL=$(dirname "$0")/../pr-review/scripts/trivial-check.py
ROW_STATE=$(dirname "$0")/row-state.py
eval "$(python3 "$KIT/context-db/bin/kit_profile.py" gh-env)"  # github.sandbox_token_prefix, if any
REPOS=()
while IFS= read -r _repo; do REPOS+=("$_repo"); done < <(jq -r '.sweep_repos[]' "$CFG")  # a loop, not a bash-4-only array builtin — macOS ships bash 3.2
MARK_ONLY=0; QUIET=0; MARK_RUN=""; REPO_OVERRIDE=()
while [ $# -gt 0 ]; do case $1 in
  --days) DAYS=$2; shift 2;; --limit) CAP=$2; shift 2;; --repo) REPO_OVERRIDE+=("$2"); shift 2;;
  --mark-only) MARK_ONLY=1; shift;; --run) MARK_RUN=$2; shift 2;; --quiet) QUIET=1; shift;;
  *) echo "usage: pr-scan.sh [--days N] [--limit N] [--repo o/r]... [--quiet] | pr-scan.sh --mark-only --run <dir>" >&2; exit 2;; esac; done
[ ${#REPO_OVERRIDE[@]} -gt 0 ] && REPOS=("${REPO_OVERRIDE[@]}")

if [ "$MARK_ONLY" = 1 ]; then
  # the main session's step (never the forked, read-only sweep — skills/pr-scan/SKILL.md § Steps/Answer):
  # mark the NAMED run's shown-but-unsurfaced rows, from its queue.json alone — no gh call. --run is
  # required (no `latest` fallback): a sweep started after the brief was shown but before this runs must
  # never mark rows the user never saw.
  [ -n "$MARK_RUN" ] || { echo "pr-scan --mark-only: --run <dir> is required (the exact run dir the brief came from, not 'latest')" >&2; exit 2; }
  BASE_REAL=$(cd "$BASE_OUT" 2>/dev/null && pwd -P) || { echo "pr-scan --mark-only: cannot resolve $BASE_OUT" >&2; exit 2; }
  DIR=$(cd "$MARK_RUN" 2>/dev/null && pwd -P) || { echo "pr-scan --mark-only: no such dir: $MARK_RUN" >&2; exit 5; }
  case "$DIR" in
    "$BASE_REAL"/*) ;;
    *) echo "pr-scan --mark-only: $MARK_RUN resolves outside $BASE_OUT — refusing" >&2; exit 2 ;;
  esac
  Q=$DIR/queue.json
  [ -f "$Q" ] || { echo "pr-scan --mark-only: $Q missing — the sweep that wrote $DIR did not finish" >&2; exit 5; }
  # claim_owner is the same exclusive-create primitive with_lock uses for its lock dir: the first
  # --mark-only call on this run dir wins, a second (a re-armed loop tick firing twice, a retry after
  # success) is a no-op instead of a double append.
  if ! claim_owner "$DIR/.marked"; then
    [ "$QUIET" = 1 ] || echo "pr-scan --mark-only: $DIR already marked"
    exit 0
  fi
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  rows=$(jq -c --arg ts "$ts" --argjson m "$MAXROWS" '.[:$m][] | select(.surfaced|not) | {repo, pr, head, status:"surfaced", ts:$ts, src, kind, auto:(.auto.eligible // false)}' "$Q") \
    || { echo "pr-scan --mark-only: could not render the surfaced rows from $Q" >&2; rm -f "$DIR/.marked"; exit 5; }
  if [ -z "$rows" ]; then
    [ "$QUIET" = 1 ] || echo "pr-scan --mark-only: nothing new to mark"
    exit 0
  fi
  printf '%s\n' "$rows" | bash "$LEDGER_APPEND" "$LEDGER"; rc=$?
  if [ "$rc" != 0 ]; then
    rm -f "$DIR/.marked"   # the append failed — a retry must be allowed to try again, not read as "already marked"
    echo "pr-scan --mark-only: ledger-append.sh failed (rc=$rc) — see its stderr above" >&2
  else
    [ "$QUIET" = 1 ] || echo "pr-scan --mark-only: marked $(wc -l <<<"$rows" | tr -d ' ') row(s)"
  fi
  exit $rc
fi

find "$BASE_OUT" -mindepth 1 -maxdepth 1 -type d -name 'run.*' -mtime +7 -exec rm -rf {} + 2>/dev/null
OUT=$(mktemp -d "$BASE_OUT/run.$(date -u +%Y%m%dT%H%M%SZ).XXXX"); ln -sfn "$OUT" "$BASE_OUT/latest"
ERR=$OUT/errors.txt; : > "$ERR"; : > "$ERR.raw"
# epoch arithmetic + epoch_to_iso, not `date -d "-N days"` (GNU-only — BSD `date` has no `-d`)
since=$(epoch_to_iso "$(( $(date -u +%s) - DAYS * 86400 ))")

# one scan at a time — a second sweep would double-mark the ledger; with_lock falls back to an
# atomic mkdir where this host has no flock (macOS without coreutils)
with_lock "$ROOT/.scan.lock" || { echo "error: another pr-scan holds $ROOT/.scan.lock — not starting a second sweep" >&2; exit 3; }

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
# fetch_threads <repo> <num> <key> — reviewThreads, cursor-paginated like pr-merge.sh's open_threads
# and reply-threads.sh (a bare first:100 silently truncates a PR with >100 threads, hiding a "mine"
# open thread on page 2 forever). Sets $RESP to the merged nodes array on success; reuses gh_json's
# retry/FAILS accounting per page, so a page that never recovers counts as an ordinary error.
fetch_threads(){
  local repo=$1 num=$2 key=$3 o=${1%/*} r=${1#*/} cursor="" raw="" page
  while :; do
    if [ -n "$cursor" ]; then
      gh_json "threads $key" api graphql -f query="$threads_q2" -F o="$o" -F r="$r" -F n="$num" -F c="$cursor" || return 1
    else
      gh_json "threads $key" api graphql -f query="$threads_q1" -F o="$o" -F r="$r" -F n="$num" || return 1
    fi
    page=$RESP
    raw+=$(jq -c '.data.repository.pullRequest.reviewThreads.nodes[]?' <<<"$page")$'\n'
    [ "$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' <<<"$page")" = true ] || break
    cursor=$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor' <<<"$page")
  done
  RESP=$(jq -sc '[.[]]' <<<"$raw")
}

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
for r in ${REPOS[@]+"${REPOS[@]}"}; do  # bash 3.2 (macOS): an empty array under set -u is "unbound"
  gh_json "list $r" pr list --repo "$r" --state open --draft=false --limit 100 --json number,updatedAt,isDraft,author,headRefOid || continue
  rows=$(jq -c --arg r "$r" --arg me "$ME" --arg since "$since" --argjson bots "$BOTS" --argjson led "$LEDGER_JSON" '
    .[] | select(.author.login!=$me)
    | . as $p | .author.login as $a | ((.author.is_bot // false) or (($bots|index($a))!=null)) as $isbot
    | ($led | map(select(.repo==$r and .pr==$p.number and .head==$p.headRefOid and (.status|IN("skipped","shadow_approve","auto_approved","shadow_comment")))) | length) as $term
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
: > "$OUT/candidates.jsonl"; n=0; dropped_bot=$pre_bot; dropped_stale=$pre_stale; dropped_draft=0; dropped_done=$pre_done; dropped_skip=0; dropped_approved=0; dropped_cap=0; dropped_fail=0; degraded=0; ac_count=0
threads_q1='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100){pageInfo{hasNextPage endCursor} nodes{isResolved opener:comments(first:1){nodes{author{login}}} last:comments(last:1){nodes{author{login}}}}}}}}'
threads_q2='query($o:String!,$r:String!,$n:Int!,$c:String){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100,after:$c){pageInfo{hasNextPage endCursor} nodes{isResolved opener:comments(first:1){nodes{author{login}}} last:comments(last:1){nodes{author{login}}}}}}}}'
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
    auto_raw=$(python3 "$TRIVIAL" "$repo" "$num" 2>>"$ERR.raw" || true)   # exit 1 = gh/API failure, exit 3 = config/setup problem; both still print a JSON with reasons
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
  # our own review on THIS head: the standing verdict (APPROVED / CHANGES_REQUESTED — a later COMMENT of ours does
  # not replace it), else our last review there; null when we have none on this head
  mine_cur=$(jq -c --arg me "$ME" --arg h "$head" 'def rh: ((((.body // "") | [capture("pr-review:v1 head=(?<h>[0-9a-f]+)")] | first | .h) // .commit_id));
    [.[] | select(.user.login==$me and .state!="PENDING" and rh==$h)] as $cur
    | (([$cur[] | select(.state=="APPROVED" or .state=="CHANGES_REQUESTED")] | last) // ($cur | last) // null)
    | if .==null then null else {state, review_id: .id} end' <<<"$reviews")
  humans=$(jq -c --argjson b "$BOTS" --arg me "$ME" '[.[] | select((.user.login as $u | ($b|index($u))|not) and .user.login!=$me and (.state=="APPROVED" or .state=="CHANGES_REQUESTED" or (.state=="COMMENTED" and ((.body//"")|length)>0)))] | group_by(.user.login) | map({login: .[0].user.login, state: (last.state)})' <<<"$reviews")
  approved=$(jq -r '[.[] | select(.state=="APPROVED")] | length' <<<"$humans")
  changes=$(jq -r '[.[] | select(.state=="CHANGES_REQUESTED")] | length' <<<"$humans")
  threads='{"open":null,"mine":null}'
  if fetch_threads "$repo" "$num" "$key"; then
    threads=$(jq -c --arg me "$ME" '[.[] | select(.isResolved|not)] | {open: length, mine: ([.[] | select(.opener.nodes[0].author.login==$me and .last.nodes[0].author.login!=$me)] | length)}' <<<"$RESP")
  else deg=$(jq -c '. + ["threads"]' <<<"$deg"); fi
  # ledger: every status ever recorded for THIS head (order-independent — no grep on serialised key order)
  lset=$(jq -c --arg repo "$repo" --argjson pr "$num" --arg h "$head" '[.[] | select(.repo==$repo and .pr==$pr and .head==$h)]' <<<"$LEDGER_JSON")
  has(){ jq -e --arg s "$1" 'map(.status) | index($s)' >/dev/null <<<"$lset"; }
  lstatus=$(jq -r 'map(.status) | (if index("reviewed") then "reviewed" elif index("replied") then "replied" elif index("skipped") then "skipped" elif index("auto_approved") then "auto_approved" elif index("shadow_approve") then "shadow_approve" elif index("shadow_fallback") then "shadow_fallback" elif index("held") then "held" elif index("shadow_comment") then "shadow_comment" elif index("surfaced") then "surfaced" else "" end)' <<<"$lset")
  if [ "$my_head" = "$head" ] || has reviewed || has auto_commented; then
    vstate=$(jq -r '.state // ""' <<<"$mine_cur")
    if [ "$(jq -r .mine <<<"$threads")" != "0" ] && [ "$(jq -r .mine <<<"$threads")" != "null" ]; then kind=follow_up
    elif has skipped || has shadow_approve || has auto_approved || has shadow_comment; then dropped_done=$((dropped_done+1)); continue
    elif [ "$vstate" = APPROVED ] || [ "$vstate" = CHANGES_REQUESTED ]; then kind="done"   # our verdict stands on this head: watched while the PR is open
    elif { has reviewed || has auto_commented; } && ! jq -e '[.[] | select(.status=="surfaced" and .kind=="done")] | length > 0' >/dev/null <<<"$lset"; then kind="done"   # a review the kit posted: reported once
    else dropped_done=$((dropped_done+1)); continue; fi
  elif [ -n "$my_head" ]; then kind=re_review
  elif has skipped; then dropped_skip=$((dropped_skip+1)); continue
  elif has shadow_approve || has auto_approved || has shadow_comment; then dropped_done=$((dropped_done+1)); continue   # shadow_fallback stays: it is an ordinary row; a `held` row stays too (interactive pr-review still needs it)
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
  # a done row costs no enrich slot (it was dropped before it was kept) and never rides an auto path
  if [ "$kind" = "done" ]; then auto='null'; else n=$((n+1)); fi
  hlen=$(jq -r 'length' <<<"$humans")
  if [ "$kind" = "done" ]; then prio_val=6
  elif [ "$src" = direct ]; then prio_val=1
  elif [ "$kind" != new ]; then prio_val=2
  elif [ "$src" = team ] && [ "$hlen" = 0 ]; then prio_val=3
  elif [ "$hlen" = 0 ]; then prio_val=4
  else prio_val=5; fi
  # unattended auto-COMMENT gate (opt-in, `auto_comment.mode`): direct review requests only by default
  # (`prios`), never a follow-up, never a head we already reviewed, never a re-request on a PR we already
  # reviewed (unless `auto_comment.include_re_review`), never a bot author, never a head already `held` (a
  # STOP finding sent it back to the interactive walk — it stays an ordinary row but never re-counts toward
  # the cap), capped at `max_per_tick` runners this tick.
  ac_eligible=false; ac_reason="mode off"
  if [ "$AUTOC_MODE" != off ]; then
    if [ "$kind" = follow_up ]; then ac_reason="follow-up"
    elif [ "$kind" = "done" ]; then ac_reason="already reviewed"
    elif [ "$kind" = re_review ] && [ "$AUTOC_REREVIEW" != true ]; then ac_reason="re-review"
    elif has held; then ac_reason="held"
    elif in_list "$author" "$BOTS"; then ac_reason="bot author"
    elif ! jq -ne --argjson p "$prio_val" --argjson list "$AUTOC_PRIOS" '$list | index($p) != null' >/dev/null; then ac_reason="prio $prio_val not in auto_comment.prios"
    elif [ "$ac_count" -ge "$AUTOC_MAX" ]; then ac_reason="max_per_tick reached"
    else ac_eligible=true; ac_reason=""; ac_count=$((ac_count+1))
    fi
  fi
  jq -nc --arg repo "$repo" --argjson pr "$num" --arg head "$head" --arg src "$src" --arg kind "$kind" --arg author "$author" \
    --arg title "$(jq -r .title <<<"$pr")" --arg upd "$upd" --arg base "$(jq -r .base.ref <<<"$pr")" \
    --argjson add "$(jq .additions <<<"$pr")" --argjson del "$(jq .deletions <<<"$pr")" --argjson files "$(jq .changed_files <<<"$pr")" \
    --argjson humans "$humans" --argjson approved "$approved" --argjson changes "$changes" --argjson threads "$threads" \
    --arg bot "$bot" --arg lstatus "$lstatus" --argjson surfaced "$surfaced" --argjson deg "$deg" --argjson deep "$DEEP" --arg url "$(jq -r .html_url <<<"$pr")" --argjson auto "$auto" \
    --argjson req "$(jq -c '[.requested_reviewers[].login] + [.requested_teams[].slug | "@"+.]' <<<"$pr")" \
    --argjson prio "$prio_val" --argjson mine "$mine_cur" --argjson ac_eligible "$ac_eligible" --arg ac_reason "$ac_reason" \
    '{repo:$repo, pr:$pr, head:$head, src:$src, kind:$kind, author:$author, title:$title, updated:$upd, base:$base,
      lines:($add+$del), files:$files, humans:$humans, approved:$approved, changes_requested:$changes, threads:$threads,
      bot:$bot, ledger:$lstatus, surfaced:$surfaced, degraded:$deg, deep:(($add+$del)>$deep), url:$url, requested:$req, auto:$auto,
      auto_comment: {eligible:$ac_eligible, reason:$ac_reason},
      mine:$mine, prio:$prio}' >> "$OUT/candidates.jsonl"
done < "$OUT/union.jsonl"

# 4. rank + present — `new` and `auto` are counted over the SHOWN rows, which are exactly the rows --mark-only records
jq -s 'sort_by([.prio, (.updated|explode|map(-.))])' "$OUT/candidates.jsonl" > "$OUT/queue.json"
# one `state` (+ `section`, `state_label`) per row from the row's own kind / review and the ledger's entries
# for that PR — no extra gh call, the brief renders by `.section` instead of inventing the grouping itself.
# A failure counts as a sweep error: the rows then carry no section, and that must not read as a clean tick.
if python3 "$ROW_STATE" --queue "$OUT/queue.json" --ledger "$LEDGER" > "$OUT/queue.json.state" 2>>"$ERR.raw"; then
  mv "$OUT/queue.json.state" "$OUT/queue.json"
else
  FAILS=$((FAILS+1)); echo "FAIL row-state: rows carry no state/section: $(tail -c 160 "$ERR.raw" | tr '\n' ' ')" >> "$ERR"; rm -f "$OUT/queue.json.state"
fi
shown=$(jq --argjson m "$MAXROWS" '.[:$m] | length' "$OUT/queue.json"); cand=$(jq length "$OUT/queue.json")
newc=$(jq --argjson m "$MAXROWS" '[.[:$m][] | select((.surfaced|not) and .kind!="done")] | length' "$OUT/queue.json")
autoc=$(jq --argjson m "$MAXROWS" '[.[:$m][] | select(.auto.eligible // false)] | length' "$OUT/queue.json")
autocmt=$(jq --argjson m "$MAXROWS" '[.[:$m][] | select(.auto_comment.eligible // false)] | length' "$OUT/queue.json")
if [ "$QUIET" = 0 ]; then
  printf '%-4s %-28s %-10s %-4s %-18s %-9s %-7s %-14s %-24s %s\n' PRIO PR SRC KIND AUTHOR LINES/F HUMANS THREADS/BOT STATE TITLE
  jq -r --argjson m "$MAXROWS" '.[:$m][]
      | (.repo|split("/")[1]) as $r | (if .surfaced then "" else " *" end) as $star
      | (if .deep then "!" elif (.auto.eligible // false) then "A" elif (.auto_comment.eligible // false) then "C" else "" end) as $bang
      | (if (.degraded|index("reviews")) then "?" else (.humans|map(.state[:3])|join(",")) end) as $h
      | ((.threads.open // "?")|tostring) as $t | ({green:"🟢",yellow:"🟡",red:"🔴"}[.bot] // "-") as $b
      | [(.prio|tostring), ($r+"#"+(.pr|tostring)+$star), .src, .kind[:4], .author[:18], ((.lines|tostring)+"/"+(.files|tostring)+$bang), (if $h=="" then "-" else $h end), ($t+"/"+$b), (.state_label // "?"), .title[:60]] | @tsv' "$OUT/queue.json" \
    | awk -F'\t' '{printf "%-4s %-28s %-10s %-4s %-18s %-9s %-7s %-14s %-24s %s\n",$1,$2,$3,$4,$5,$6,$7,$8,$9,$10}'
  echo "-- * = not surfaced before on this head; ! = over deep threshold ($DEEP lines); A = trivial-PR auto-approve eligible (mode=$AUTO_MODE); C = unattended auto-COMMENT eligible (mode=$AUTOC_MODE); ? = review state unavailable (see errors); LINES/F = added+deleted / files; HUMANS = last state per human reviewer; STATE = brief section (SKILL.md § Answer)"
fi
echo "summary: union=$total candidates=$cand shown=$shown new=$newc auto=$autoc auto_mode=$AUTO_MODE auto_comment=$autocmt auto_comment_mode=$AUTOC_MODE dropped(bots=$dropped_bot draft=$dropped_draft stale>${DAYS}d=$dropped_stale done=$dropped_done skipped=$dropped_skip approved=$dropped_approved cap=$dropped_cap failed=$dropped_fail) degraded=$degraded errors=$FAILS retries=$RETRIES out=$OUT"
# the sweep itself never writes the ledger — run `pr-scan.sh --mark-only` as a separate step (see header)
[ "$FAILS" -eq 0 ]
