#!/usr/bin/env bash
# fetch-context.sh <owner/repo> <pr> [--out DIR] [--no-bundle]
# Snapshots everything a review needs into one directory and decides the review mode. Read-only.
# Fail-closed: a fetch that fails after a retry aborts (exit 1) — a swallowed error must never read as "no reviews".
# Besides the snapshot it writes the BUNDLE the runner works from, so no model turn is spent on `gh api contents`
# or `git show`: head/<path> and base/<path> for every touched file, diffs/<path>.patch, checks+statuses,
# kb-traps.md (repo KB § Known traps + sections matching touched paths), bundle.json (per-file stats, unresolved
# threads with their last comment). Prints the manifest summary; the SKILL tells the model what to do with it.
set -uo pipefail
KIT="$(cd "$(dirname "$0")/../../.." && pwd)"
# the workspace's .context/ (#74) the way every kit script finds it (kit_profile.py context), never from this
# script's location: on a plugin install that is Claude Code's plugin cache, wiped on update. PR_REVIEW_HOME overrides.
CTX=$(python3 "$KIT/context-db/bin/kit_profile.py" context)
[ -n "${PR_REVIEW_HOME:-}" ] || [ -d "$CTX" ] || { echo "fetch-context.sh: no workspace .context/ found ($CTX) — run from the workspace, or set PR_REVIEW_HOME" >&2; exit 2; }
export PR_REVIEW_HOME=${PR_REVIEW_HOME:-$CTX/state/pr-review}; ROOT=$PR_REVIEW_HOME
eval "$(python3 "$KIT/context-db/bin/kit_profile.py" gh-env)"  # github.sandbox_token_prefix, if any
ME=$(jq -r .login "$ROOT/config.json"); BOTS=$(jq -c .bots "$ROOT/config.json")
BMAXF=$(jq -r '.bundle_max_files // 60' "$ROOT/config.json"); BMAXKB=$(jq -r '.bundle_max_kb // 200' "$ROOT/config.json")
repo=${1:?usage: fetch-context.sh <owner/repo> <pr> [--out DIR] [--no-bundle]}; pr=${2:?pr number}; shift 2; out=""; bundle=1
while [ $# -gt 0 ]; do case $1 in --out) out=$2; shift 2;; --no-bundle) bundle=0; shift;; *) echo "unknown arg $1" >&2; exit 2;; esac; done
if [ -z "$out" ]; then base=$(python3 "$(dirname "$0")/../../../context-db/bin/kit_profile.py" scratch pr-review) && [ -n "$base" ] || { echo "error: cannot resolve the snapshot dir (kit_profile.py scratch failed)" >&2; exit 2; }; out="$base/${repo//\//_}/$pr"; fi
mkdir -p "$out"; ERR=$out/errors.txt; : > "$ERR"
fail(){ echo "error: $*" >&2; echo "FATAL: $*" >> "$ERR"; exit 1; }
# get <args…>: 2 attempts, valid non-empty output required; stdout = the response
get(){ local i rc o; for i in 1 2; do o=$(gh api "$@" 2>>"$ERR"); rc=$?; if [ $rc -eq 0 ] && [ -n "$o" ]; then printf '%s' "$o"; return 0; fi; [ $i -eq 1 ] && sleep 2; done; echo "FAIL gh api $* (rc=$rc)" >> "$ERR"; return 1; }
pages(){ local o; o=$(get --paginate "$1") || return 1; jq -sc 'add // []' <<<"$o" || return 1; }

get "repos/$repo/pulls/$pr" > "$out/pr.json" || fail "cannot fetch repos/$repo/pulls/$pr (see $ERR)"
jq -e .head.sha "$out/pr.json" >/dev/null || fail "pr.json has no head.sha"
head=$(jq -r .head.sha "$out/pr.json"); base=$(jq -r .base.sha "$out/pr.json"); o=${repo%/*}; r=${repo#*/}
pages "repos/$repo/pulls/$pr/files?per_page=100" > "$out/files.json" || fail "cannot fetch the file list"
get "repos/$repo/pulls/$pr" -H 'Accept: application/vnd.github.diff' > "$out/diff.patch" || { echo "diff fetch failed (huge PR?) — per-file patches in files.json/diffs/ still apply" >> "$ERR"; : > "$out/diff.patch"; }
pages "repos/$repo/pulls/$pr/reviews?per_page=100" > "$out/reviews.json" || fail "cannot fetch reviews — mode would be wrong"
pages "repos/$repo/pulls/$pr/comments?per_page=100" > "$out/review_comments.json" || fail "cannot fetch review comments"
pages "repos/$repo/issues/$pr/comments?per_page=100" > "$out/issue_comments.json" || fail "cannot fetch issue comments"
# checks: check-runs (paginated) + the commit status API (external CI posts there, not as check-runs)
cr=$(get --paginate "repos/$repo/commits/$head/check-runs?per_page=100") || fail "cannot fetch check-runs"
jq -sc '[.[].check_runs[]? | {name, status, conclusion, app: .app.slug, url: .html_url}]' <<<"$cr" > "$out/checks.json" || fail "check-runs unparseable"
st=$(get "repos/$repo/commits/$head/status") || fail "cannot fetch the combined status"
jq -c '{state, total_count, statuses: [.statuses[]? | {context, state, target_url}]}' <<<"$st" > "$out/status.json"

# review threads (GraphQL, cursor-paginated; comments capped at 50 per thread)
q='query($o:String!,$r:String!,$n:Int!,$c:String){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:50,after:$c){pageInfo{hasNextPage endCursor}nodes{id isResolved isOutdated path line originalLine diffSide comments(first:50){nodes{databaseId author{login} body createdAt url}}}}}}}'
cursor=""; : > "$out/threads.raw"; complete=true
while :; do
  if [ -n "$cursor" ]; then resp=$(get graphql -f query="$q" -F o="$o" -F r="$r" -F n="$pr" -F c="$cursor"); else resp=$(get graphql -f query="$q" -F o="$o" -F r="$r" -F n="$pr"); fi
  [ -z "$resp" ] && { complete=false; break; }
  jq -c '.data.repository.pullRequest.reviewThreads.nodes[]?' <<<"$resp" >> "$out/threads.raw"
  if [ "$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' <<<"$resp")" = "true" ]; then cursor=$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor' <<<"$resp"); else break; fi
done
jq -s --arg me "$ME" '[.[] | {id, is_resolved: .isResolved, is_outdated: .isOutdated, path, line: (.line // .originalLine), side: .diffSide,
   root_author: .comments.nodes[0].author.login, root_id: .comments.nodes[0].databaseId, mine: (.comments.nodes[0].author.login==$me),
   last_author: (.comments.nodes[-1].author.login), n_comments: (.comments.nodes|length),
   comments: [.comments.nodes[] | {id: .databaseId, author: .author.login, body, created_at: .createdAt, url}]}]' "$out/threads.raw" > "$out/threads.json"
rm -f "$out/threads.raw"

# mode decision: my last review + snapshot marker
mine=$(jq -c --arg me "$ME" '[.[] | select(.user.login==$me and .state!="PENDING")] | last // null' "$out/reviews.json")
marker_head=$(jq -r 'if .==null then "" else (((.body // "") | [capture("pr-review:v1 head=(?<h>[0-9a-f]+) base=(?<b>[0-9a-f]+)")] | first | .h) // "") end' <<<"$mine")
my_commit=$(jq -r 'if .==null then "" else .commit_id end' <<<"$mine")
if [ -z "$my_commit" ]; then mode=full
elif [ "${marker_head:-$my_commit}" = "$head" ]; then mode=replies_only
else mode=follow_up; fi
# bot verdict: one helper, shared with pr-scan/pr-watch/pr-merge/trivial-check — pass the reviews
# we already fetched so this costs no extra gh call; exit 2 (no bot configured) reads as "n/a", never a failure.
bot_err=$(mktemp)
bot=$(bash "$KIT/skills/pr-watch/bot-verdict.sh" "$repo" "$pr" "$head" "$out/reviews.json" 2>"$bot_err"); bot_rc=$?
case $bot_rc in
  0) : ;;
  2) bot="n/a" ;;
  *) fail "bot-verdict.sh failed: $(head -1 "$bot_err")" ;;
esac
rm -f "$bot_err"
jq -n --arg repo "$repo" --argjson pr "$pr" --arg head "$head" --arg base "$base" --arg mode "$mode" --arg me "$ME" \
  --argjson prj "$(jq -c '{title, state, author: .user.login, base_ref: .base.ref, default_branch: .base.repo.default_branch, head_ref: .head.ref, draft, mergeable_state, additions, deletions, changed_files, html_url, body, labels: [.labels[].name], requested_reviewers: [.requested_reviewers[].login], requested_teams: [.requested_teams[].slug], updated_at}' "$out/pr.json")" \
  --argjson mine "$mine" --arg marker_head "$marker_head" --arg bot "$bot" --argjson complete "$complete" \
  --argjson humans "$(jq -c --argjson b "$BOTS" --arg me "$ME" '[.[] | select((.user.login as $u | ($b|index($u))|not) and .user.login!=$me and .state!="PENDING")] | group_by(.user.login) | map({login: .[0].user.login, state: (last.state), commit: (last.commit_id[:9])})' "$out/reviews.json")" \
  --argjson threads "$(jq -c --arg me "$ME" '{total: length, unresolved: ([.[] | select(.is_resolved|not)] | length), mine_unresolved: ([.[] | select(.mine and (.is_resolved|not))] | length), mine_awaiting_me: ([.[] | select(.mine and (.is_resolved|not) and .last_author!=$me)] | length)}' "$out/threads.json")" \
  --argjson checks "$(jq -c --slurpfile s "$out/status.json" '{total: length, not_green: [.[] | select(.status=="completed" and (.conclusion!="success" and .conclusion!="skipped" and .conclusion!="neutral")) | "\(.name): \(.conclusion)"], pending: ([.[] | select(.status!="completed")] | length), status: {state: $s[0].state, total: $s[0].total_count, not_success: [$s[0].statuses[] | select(.state!="success") | "\(.context): \(.state)"]}}' "$out/checks.json")" \
  --arg out "$out" --arg fetched "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{repo:$repo, pr:$pr, head:$head, base:$base, mode:$mode, me:$me, pr_meta:$prj,
    my_last_review:(if $mine==null then null else {id:$mine.id, state:$mine.state, commit_id:$mine.commit_id, submitted_at:$mine.submitted_at, marker_head:$marker_head, html_url:$mine.html_url} end),
    humans:$humans, bot_assessment:$bot, threads:$threads, threads_complete:$complete, checks:$checks, context_dir:$out, fetched_at:$fetched}' \
  > "$out/manifest.json" 2>>"$ERR" || fail "manifest build failed (see $ERR)"

# ---- bundle: the files at head and base, per-file patches, KB traps — fetched once, in parallel, by shell ----
bundled_head=0; bundled_base=0; skipped=0
if [ "$bundle" = 1 ]; then
  mkdir -p "$out/head" "$out/base" "$out/diffs"
  : > "$out/skipped.tsv"
  # per-file patches straight from files.json (no extra call)
  jq -r '.[] | select(.patch) | @base64' "$out/files.json" | while IFS= read -r b; do
    f=$(base64 -d <<<"$b"); p=$(jq -r .filename <<<"$f"); mkdir -p "$out/diffs/$(dirname "$p")"
    jq -r '"--- a/\(.previous_filename // .filename)\n+++ b/\(.filename)\n\(.patch)"' <<<"$f" > "$out/diffs/$p.patch"; done
  # contents at head (added/modified/renamed) and base (modified/removed/renamed) — capped by count and size.
  # files.json carries no byte size, only `.changes` (added+deleted LINES); the cap treats one line as ~80 bytes
  # (a conservative proxy — the blob itself is never larger than what a PR of that many lines touches usefully).
  # fetch_blob retries a transient failure up to 3 times, backing off longer when the error looks like a
  # rate limit (403/429) — a swallowed error must never read as "the file is unchanged".
  # PR_REVIEW_FETCH_RETRY_DELAY: base backoff in seconds (default 2; a test sets it near 0 to stay fast).
  RDELAY=${PR_REVIEW_FETCH_RETRY_DELAY:-2}
  fetch_blob(){ # fetch_blob <ref> <path> <dest>
    local o attempt err; mkdir -p "$(dirname "$3")"; err=$(mktemp "${TMPDIR:-/tmp}/fetch-blob.XXXXXX")
    for attempt in 1 2 3; do
      if o=$(gh api "repos/$repo/contents/$(jq -rn --arg p "$2" '$p|@uri')?ref=$1" -H 'Accept: application/vnd.github.raw+json' 2>"$err"); then
        printf '%s' "$o" > "$3"; rm -f "$err"; return 0
      fi
      if [ "$attempt" -lt 3 ]; then
        if grep -qiE '(^|[^0-9])(403|429)([^0-9]|$)|rate limit|retry.?after' "$err"; then sleep $((attempt * RDELAY)); else sleep "$RDELAY"; fi
      fi
    done
    echo "blob $1:$2 failed after 3 attempts: $(tr '\n' ' ' < "$err" | cut -c1-160)" >> "$ERR.blob"; rm -f "$err"; return 1
  }
  # bounded concurrency: PR_REVIEW_FETCH_CONCURRENCY background fetches at a time (default 6), never one per file —
  # a 300-file PR must not fire 300 parallel requests and trip GitHub's secondary rate limit.
  : > "$ERR.blob"; i=0; n=0; CONC=${PR_REVIEW_FETCH_CONCURRENCY:-6}
  spawn(){ "$@" & n=$((n+1)); [ $((n % CONC)) -eq 0 ] && wait; }
  while IFS=$'\t' read -r status path prev size; do
    i=$((i+1))
    if [ "$i" -gt "$BMAXF" ]; then skipped=$((skipped+1)); printf '%s\tcap: over bundle_max_files=%s\n' "$path" "$BMAXF" >> "$out/skipped.tsv"; continue; fi
    if [ $(( ${size:-0} * 80 )) -gt $((BMAXKB*1024)) ] 2>/dev/null; then
      skipped=$((skipped+1))
      printf '%s\tsize: ~%sKB > bundle_max_kb=%s\n' "$path" "$(( ${size:-0} * 80 / 1024 ))" "$BMAXKB" >> "$out/skipped.tsv"
      continue
    fi
    case "$status" in
      added)           spawn fetch_blob "$head" "$path" "$out/head/$path" ;;
      removed)         spawn fetch_blob "$base" "$path" "$out/base/$path" ;;
      renamed|copied)  spawn fetch_blob "$head" "$path" "$out/head/$path"; spawn fetch_blob "$base" "${prev:-$path}" "$out/base/${prev:-$path}" ;;
      *)               spawn fetch_blob "$head" "$path" "$out/head/$path"; spawn fetch_blob "$base" "$path" "$out/base/$path" ;;
    esac
  done < <(jq -r '.[] | [.status, .filename, (.previous_filename // ""), ((.changes // 0)|tostring)] | @tsv' "$out/files.json")
  wait
  bundled_head=$(find "$out/head" -type f | wc -l); bundled_base=$(find "$out/base" -type f | wc -l)
  if [ -s "$ERR.blob" ]; then
    while IFS= read -r ln; do
      p=$(printf '%s\n' "$ln" | sed -E 's/^blob [^:]+:(.*) failed after [0-9]+ attempts:.*/\1/')
      [ "$p" != "$ln" ] && [ -n "$p" ] && { printf '%s\tfetch-failed: after retries, see errors.txt\n' "$p" >> "$out/skipped.tsv"; skipped=$((skipped+1)); }
    done < "$ERR.blob"
    cat "$ERR.blob" >> "$ERR"
  fi
  rm -f "$ERR.blob"
  # KB traps pre-grepped: § Known traps + every section whose heading shares a word with a touched path.
  # Matched as a plain substring (index(), never a regex): a word taken from a filename may carry parentheses,
  # brackets or other regex metacharacters, and building a live pattern from it used to break the match (or the
  # whole awk program) silently — a fixed-string search cannot mis-parse.
  kb="$CTX/pr-reviews/$r.md"; : > "$out/kb-traps.md"
  if [ -f "$kb" ]; then
    words=$(jq -r '[.[] | .filename | split("/")[] | split(".")[0] | select(length>3)] | unique | .[]' "$out/files.json" | tr '\n' '|' | sed 's/|$//')
    awk -v words="$words" 'BEGIN{n = split(words, arr, "|")}
      /^## /{
        keep = ($0 ~ /[Kk]nown traps/)
        if (!keep && n > 0) {
          line = tolower($0)
          for (i = 1; i <= n; i++) { if (arr[i] != "" && index(line, tolower(arr[i])) > 0) { keep = 1; break } }
        }
      }
      keep{print}' "$kb" > "$out/kb-traps.md"
    echo "" >> "$out/kb-traps.md"; echo "<!-- source: .context/pr-reviews/$r.md — sections: Known traps + headings matching touched paths -->" >> "$out/kb-traps.md"
  fi
  jq -n --slurpfile m "$out/manifest.json" --slurpfile f "$out/files.json" --slurpfile t "$out/threads.json" --slurpfile c "$out/checks.json" --slurpfile s "$out/status.json" \
     --argjson bh "$bundled_head" --argjson bb "$bundled_base" --argjson sk "$skipped" --arg kb "$out/kb-traps.md" --arg skd "$out/skipped.tsv" \
     '{manifest: $m[0],
       files: [$f[0][] | {filename, status, additions, deletions, changes, previous_filename, head: ("head/" + .filename), base: ("base/" + (.previous_filename // .filename)), diff: ("diffs/" + .filename + ".patch")}],
       unresolved_threads: [$t[0][] | select(.is_resolved|not) | {id, path, line, root_author, last_author, mine, n_comments, last_comment: (.comments[-1] | {author, body: .body[:600], url})}],
       checks: $c[0], status: $s[0],
       bundle: {head_files: $bh, base_files: $bb, skipped: $sk, skipped_detail: $skd, kb_traps: $kb, note: "everything the review needs is under this dir — do not gh api contents / git show for touched files"}}' > "$out/bundle.json" 2>>"$ERR" || echo "bundle.json build failed" >> "$ERR"
fi

echo "context: $out"
jq -r '"\(.repo)#\(.pr) · head \(.head[:9]) · base \(.base[:9]) · mode \(.mode) · \(.pr_meta.author): \(.pr_meta.title)\n+\(.pr_meta.additions)/-\(.pr_meta.deletions) in \(.pr_meta.changed_files) files · \(.pr_meta.mergeable_state) · draft=\(.pr_meta.draft) · \(.pr_meta.state) · base \(.pr_meta.base_ref)\(if .pr_meta.base_ref!=.pr_meta.default_branch then " (STACKED, default \(.pr_meta.default_branch))" else "" end) · labels \(.pr_meta.labels|join(","))\nhumans: \(if (.humans|length)==0 then "none" else (.humans|map("\(.login)=\(.state)@\(.commit)")|join(" ")) end) · bot: \(.bot_assessment)\nthreads: \(.threads.unresolved)/\(.threads.total) unresolved (mine \(.threads.mine_unresolved), awaiting me \(.threads.mine_awaiting_me)) complete=\(.threads_complete) · checks not green: \(.checks.not_green|join("; ")|if .=="" then "none" else . end) pending=\(.checks.pending) · status \(.checks.status.state)/\(.checks.status.total)\(if (.checks.status.not_success|length)>0 then " (" + (.checks.status.not_success|join("; ")) + ")" else "" end)\nmy last review: \(if .my_last_review==null then "none" else "\(.my_last_review.state) on \(.my_last_review.commit_id[:9]) (marker \(.my_last_review.marker_head[:9]))" end)"' "$out/manifest.json"
if [ "$bundle" = 1 ]; then
  skip_summary="$skipped skipped"
  if [ "$skipped" -gt 0 ] && [ -s "$out/skipped.tsv" ]; then
    br=$(cut -f2 "$out/skipped.tsv" | cut -d: -f1 | sort | uniq -c | awk '{printf "%s %s, ", $2, $1}' | sed 's/, $//')
    skip_summary="$skipped skipped ($br)"
  fi
  echo "bundle: $bundled_head files at head · $bundled_base at base · $skip_summary · diffs/ kb-traps.md bundle.json skipped.tsv"
fi
[ -s "$ERR" ] && echo "errors: $(wc -l < "$ERR") lines in $ERR"
exit 0
