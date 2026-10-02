#!/usr/bin/env bash
# reply-threads.sh preview|submit --repo O/R --pr N --head SHA --request FILE [--confirm DIGEST]
# request FILE: {"replies":[{"thread_id":"PRRT_…","body":"…","resolve":false}]}
# Replies inside existing review threads (GraphQL). `resolve:true` is honoured ONLY for threads whose
# root comment is ours AND whose last comment is by us or the PR author — never resolve a thread that waits
# on a named third party (WORKSPACE.md § Rules). Thread state is cursor-paginated; the `replied` ledger row is
# written only when at least one reply actually landed. Bodies get the same local-path / bare-Jira-key lint as reviews.
set -uo pipefail
KIT="$(cd "$(dirname "$0")/../../.." && pwd)"
# shellcheck source=../../_lib/portable.sh
. "$KIT/skills/_lib/portable.sh"  # claim_owner — the digest lock below needs exclusive create, not a plain `mkdir`
eval "$(python3 "$KIT/context-db/bin/kit_profile.py" gh-env)"  # github.sandbox_token_prefix, if any
# the workspace's .context/ (#74) the way every kit script finds it (kit_profile.py context), never from this
# script's location: on a plugin install that is Claude Code's plugin cache, wiped on update. PR_REVIEW_HOME overrides.
CTX=$(python3 "$KIT/context-db/bin/kit_profile.py" context)
[ -n "${PR_REVIEW_HOME:-}" ] || [ -d "$CTX" ] || { echo "reply-threads.sh: no workspace .context/ found ($CTX) — run from the workspace, or set PR_REVIEW_HOME" >&2; exit 2; }
export PR_REVIEW_HOME=${PR_REVIEW_HOME:-$CTX/state/pr-review}; ROOT=$PR_REVIEW_HOME
ME=$(jq -r .login "$ROOT/config.json"); LEDGER=$ROOT/ledger.jsonl
usage(){ echo "usage: reply-threads.sh preview|submit --repo O/R --pr N --head SHA --request FILE [--confirm DIGEST]" >&2; exit 2; }
cmd=${1:-}; shift || usage; repo=""; pr=""; head=""; req=""; confirm=""
while [ $# -gt 0 ]; do case $1 in
  --repo) repo=$2; shift 2;; --pr) pr=$2; shift 2;; --head) head=$2; shift 2;; --request) req=$2; shift 2;; --confirm) confirm=$2; shift 2;; *) usage;; esac; done
[ "$cmd" = preview ] || [ "$cmd" = submit ] || usage
[ -n "$repo" ] && [ -n "$pr" ] && [ -n "$head" ] && [ -f "$req" ] || usage
jq -e '.replies|type=="array" and length>0 and all(.[]; (.thread_id|type=="string" and startswith("PRRT_")) and (.body|type=="string" and length>0) and ((.resolve // false)|type=="boolean"))' "$req" >/dev/null \
  || { echo "error: request shape invalid ({replies:[{thread_id,body,resolve}]})" >&2; exit 2; }
o=${repo%/*}; r=${repo#*/}
leaks=$(jq -r '[.replies[].body] | map(select(test("(^|[^A-Za-z0-9_])\\.context/|scratchpad|/tmp/|\\.cache/tmp|(claude|ai-baton)-kit/|/run/user/|/Users/[a-z]|/home/[a-z]|(^|[^A-Za-z0-9_])\\.claude/"))) | length' "$req")
[ "$leaks" = 0 ] || { echo "error: $leaks reply body(ies) mention a local workspace path — link the PR/ticket or inline the evidence instead" >&2; exit 2; }
# bare tracker keys: this lint needs a real env store (tracker.kind + key_regex). Exactly three cases:
#   - `kit_profile.py source` itself fails (nonzero exit)     → fatal, the store is broken.
#   - it says "none" (no store at all)                        → nothing is configured anywhere; `get` would
#     document that with a stderr warning + exit 1 ("kit defaults apply") — that is NOT an error, so check
#     `source` first and skip the lint entirely rather than read the warning as a failure.
#   - it says "env" (a real store exists)                     → a GitHub `#123` auto-links, so the lint still
#     does not apply when `tracker.kind` is github; every other kind gates on `tracker.key_regex` being
#     configured (the field the lint actually uses — a Linear-style key is bare-shaped too). Here a `get`
#     lookup's exit 1 means "absent" ONLY when it wrote nothing to stderr (an ordinary missing key); exit 1
#     WITH stderr (the Python-floor guard, an uncaught traceback) or any exit above 1 is a real failure and
#     must stop here, never post with bare keys (WORKSPACE.md § Verification).
kget(){ # kget <dotted.key> -> value on stdout; rc 0 ok, 1 genuinely absent (empty stdout), 2 lookup failed (message on stderr)
  local v rc kerr; kerr=$(mktemp)
  v=$(python3 "$KIT/context-db/bin/kit_profile.py" get "$1" 2>"$kerr"); rc=$?
  if [ $rc -eq 0 ]; then rm -f "$kerr"; printf '%s' "$v"; return 0; fi
  if [ $rc -eq 1 ] && [ ! -s "$kerr" ]; then rm -f "$kerr"; return 1; fi
  echo "error: $1 unreadable: $(cat "$kerr")" >&2; rm -f "$kerr"; return 2
}
err=$(mktemp)
SRC=$(python3 "$KIT/context-db/bin/kit_profile.py" source 2>"$err"); rc=$?
[ $rc -ne 0 ] && { echo "error: kit_profile.py source failed: $(cat "$err")" >&2; rm -f "$err"; exit 2; }
rm -f "$err"
if [ "$SRC" = env ]; then
  TRK_KIND=$(kget tracker.kind); rc=$?; [ $rc -eq 2 ] && exit 2
  if [ "$TRK_KIND" != github ]; then
    TRK_RE=$(kget tracker.key_regex); rc=$?; [ $rc -eq 2 ] && exit 2
    if [ -n "$TRK_RE" ]; then
      bare=$(jq -r --arg re "$TRK_RE" '[.replies[].body] | map(gsub("\\[[^\\]]*\\]\\([^)]*\\)";"") | gsub("https?://[^ )>]+";"") | select(test($re))) | length' "$req")
      [ "$bare" = 0 ] || { echo "error: $bare reply body(ies) carry a bare tracker key — make it a link ([KEY](tracker.url_template))" >&2; exit 2; }
    fi
  fi
fi
live=$(gh api "repos/$repo/pulls/$pr" 2>/dev/null) || { echo "error: cannot read PR" >&2; exit 3; }
live_head=$(jq -r .head.sha <<<"$live"); pr_author=$(jq -r .user.login <<<"$live")
[ "$live_head" = "$head" ] || { echo "error: head moved $head -> $live_head; re-fetch context and re-read the threads first" >&2; exit 3; }
# fresh thread state (cursor-paginated)
q='query($o:String!,$r:String!,$n:Int!,$c:String){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100,after:$c){pageInfo{hasNextPage endCursor}nodes{id isResolved path line comments(first:50){nodes{author{login} body}}}}}}}'
cursor=""; raw=""
while :; do
  if [ -n "$cursor" ]; then page=$(gh api graphql -f query="$q" -F o="$o" -F r="$r" -F n="$pr" -F c="$cursor" 2>/dev/null); else page=$(gh api graphql -f query="$q" -F o="$o" -F r="$r" -F n="$pr" 2>/dev/null); fi
  jq -e '.data.repository.pullRequest.reviewThreads' <<<"$page" >/dev/null 2>&1 || { echo "error: could not read thread state via GraphQL; do not reply from a degraded snapshot" >&2; exit 3; }
  raw+=$(jq -c '.data.repository.pullRequest.reviewThreads.nodes[]?' <<<"$page")$'\n'
  [ "$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' <<<"$page")" = true ] || break
  cursor=$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor' <<<"$page")
done
threads=$(jq -sc '[.[] | {id, is_resolved: .isResolved, path, line, root_author: .comments.nodes[0].author.login, last_author: .comments.nodes[-1].author.login, n: (.comments.nodes|length)}]' <<<"$raw")
plan=$(jq -c --argjson t "$threads" --arg me "$ME" --arg pa "$pr_author" '[.replies[] | . as $rp | ($t | map(select(.id==$rp.thread_id)) | first) as $th |
  {thread_id: $rp.thread_id, body: $rp.body, resolve: ($rp.resolve // false), path: $th.path, line: $th.line, root_author: $th.root_author, last_author: $th.last_author, is_resolved: $th.is_resolved,
   problem: (if $th==null then "thread not found on this PR" elif $th.is_resolved then "thread already resolved"
             elif ($rp.resolve // false) and $th.root_author!=$me then "resolve refused: root comment is by \($th.root_author), not us"
             elif ($rp.resolve // false) and ($th.last_author|IN($me,$pa)|not) then "resolve refused: last comment is by \($th.last_author) (third party) — the open thread is the gate"
             else "" end)}]' "$req")
bad=$(jq -r '[.[] | select(.problem!="")] | length' <<<"$plan")
digest=$(printf '%s' "$plan" | sha256sum | cut -c1-64)
if [ "$cmd" = preview ] || [ "$bad" != 0 ]; then
  echo "== reply preview for $repo#$pr @ ${head:0:9}"
  jq -r '.[] | "  - \(.thread_id) \(.path // "?"):\(.line // "?") root=\(.root_author // "?") last=\(.last_author // "?") resolve=\(.resolve)\(if .problem!="" then "  !! " + .problem else "" end)\n      \(.body|split("\n")|join("\n      "))"' <<<"$plan"
  [ "$bad" != 0 ] && { echo "error: $bad reply/replies refused — fix the request"; exit 2; }
  echo "digest: $digest"; echo "submit with: reply-threads.sh submit --repo $repo --pr $pr --head $head --request $req --confirm $digest"; exit 0
fi
[ "$confirm" = "$digest" ] || { echo "error: --confirm does not match current digest $digest" >&2; exit 4; }
lock="$ROOT/.submitted/reply-$digest"; mkdir -p "$ROOT/.submitted"
# the same two gates as submit-review.sh: an existing dir refuses (a batch submitted earlier), and of
# two concurrent submits only the one that creates $lock/claimed holds the lock.
{ mkdir "$lock" 2>/dev/null && claim_owner "$lock/claimed"; } || { echo "error: this exact batch was already submitted ($lock)" >&2; exit 5; }
mrep='mutation($t:ID!,$b:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$t,body:$b}){comment{databaseId url}}}'
mres='mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{isResolved}}}'
ok=0; failed=0
while IFS= read -r item; do
  tid=$(jq -r .thread_id <<<"$item"); body=$(jq -r .body <<<"$item"); res=$(jq -r .resolve <<<"$item")
  out=$(gh api graphql -f query="$mrep" -F t="$tid" -f b="$body" 2>"$lock/err-$tid") || { echo "  FAILED reply $tid: $(head -c 300 "$lock/err-$tid")"; failed=$((failed+1)); continue; }
  url=$(jq -r '.data.addPullRequestReviewThreadReply.comment.url' <<<"$out"); ok=$((ok+1))
  line="  replied $tid → $url"
  if [ "$res" = "true" ]; then
    if gh api graphql -f query="$mres" -F t="$tid" >/dev/null 2>>"$lock/err-$tid"; then line="$line · resolved"; else line="$line · RESOLVE FAILED"; fi
  fi
  echo "$line"
done < <(jq -c '.[]' <<<"$plan")
echo "status: $([ $failed = 0 ] && echo submitted || echo partial) · replies $ok · failed $failed"
if [ "$ok" -ge 1 ]; then
  ( flock 9; jq -nc --arg repo "$repo" --argjson pr "$pr" --arg head "$head" --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson n "$ok" '{repo:$repo, pr:$pr, head:$head, status:"replied", ts:$ts, replies:$n}' >> "$LEDGER" ) 9>"$ROOT/.ledger.lock"
else
  rm -rf "$lock"   # nothing landed — release the batch lock so a corrected request can be submitted
fi
[ $failed = 0 ]
