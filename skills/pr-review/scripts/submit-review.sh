#!/usr/bin/env bash
# submit-review.sh preview|submit --repo O/R --pr N --head SHA --request FILE [--confirm DIGEST] [--auto] [--auto-comment]
# request FILE: {"event":"COMMENT|APPROVE|REQUEST_CHANGES","body":"…","comments":[{"path":"…","line":N,"side":"RIGHT","body":"…"}]}
# preview: validates, injects the snapshot marker (no footer — owner decision 2026-09-19: attribution text only costs the reader), prints the exact payload and its digest.
# submit:  refuses unless --confirm matches the digest, the live head still equals --head, and this
#          digest has not been submitted before; posts ONE review atomically (GitHub rejects the whole
#          review with 422 if any comment cannot be anchored, so nothing half-lands); verifies afterwards.
# --auto:  the trivial-PR auto-approve path (or PR_REVIEW_AUTO=1). Refuses unless auto_approve.mode == "live" AND
#          trivial-check.py --head <sha> says eligible on the exact head being approved. Ledger status auto_approved.
# --auto-comment: the unattended auto-COMMENT path for direct review requests (or PR_REVIEW_AUTO_COMMENT=1).
#          Refuses unless the request event is exactly "COMMENT" (never APPROVE/REQUEST_CHANGES on this path),
#          auto_comment.mode == "live", and the live PR author is not the user (never the user's own PR).
#          Ledger status auto_commented. Mutually exclusive with --auto.
# lint:    body + inline comments are refused when they leak local paths (.context/, scratchpad, /tmp, ai-baton-kit/ (formerly claude-kit/), /run/user/, home dirs) or a
#          bare Jira key that is not a link — the workspace rules for every external surface.
set -uo pipefail
KIT="$(cd "$(dirname "$0")/../../.." && pwd)"
# shellcheck source=../../_lib/portable.sh
. "$KIT/skills/_lib/portable.sh"  # claim_owner — the digest lock below needs exclusive create, not a plain `mkdir`
eval "$(python3 "$KIT/context-db/bin/kit_profile.py" gh-env)"  # github.sandbox_token_prefix, if any
# the workspace's .context/ (#74) the way every kit script finds it (kit_profile.py context), never from this
# script's location: on a plugin install that is Claude Code's plugin cache, wiped on update. PR_REVIEW_HOME overrides.
CTX=$(python3 "$KIT/context-db/bin/kit_profile.py" context)
[ -n "${PR_REVIEW_HOME:-}" ] || [ -d "$CTX" ] || { echo "submit-review.sh: no workspace .context/ found ($CTX) — run from the workspace, or set PR_REVIEW_HOME" >&2; exit 2; }
export PR_REVIEW_HOME=${PR_REVIEW_HOME:-$CTX/state/pr-review}; ROOT=$PR_REVIEW_HOME
ME=$(jq -r .login "$ROOT/config.json"); FOOTER=$(jq -r '.footer // ""' "$ROOT/config.json"); LEDGER=$ROOT/ledger.jsonl
AUTO_MODE=$(jq -r '.auto_approve.mode // "off"' "$ROOT/config.json"); RET=$(jq -r '.submitted_retention_days // 14' "$ROOT/config.json")
AUTOC_MODE=$(jq -r '.auto_comment.mode // "off"' "$ROOT/config.json")
TRIVIAL=$(dirname "$0")/trivial-check.py
mkdir -p "$ROOT/.submitted"; find "$ROOT/.submitted" -mindepth 1 -maxdepth 1 -type d -mtime +"$RET" -exec rm -rf {} + 2>/dev/null
usage(){ echo "usage: submit-review.sh preview|submit --repo O/R --pr N --head SHA --request FILE [--confirm DIGEST] [--auto] [--auto-comment]" >&2; exit 2; }
cmd=${1:-}; shift || usage; repo=""; pr=""; head=""; req=""; confirm=""; auto=${PR_REVIEW_AUTO:-0}; autoc=${PR_REVIEW_AUTO_COMMENT:-0}
while [ $# -gt 0 ]; do case $1 in
  --repo) repo=$2; shift 2;; --pr) pr=$2; shift 2;; --head) head=$2; shift 2;; --request) req=$2; shift 2;; --confirm) confirm=$2; shift 2;; --auto) auto=1; shift;; --auto-comment) autoc=1; shift;; *) usage;; esac; done
[ "$auto" = 1 ] && [ "$autoc" = 1 ] && { echo "error: --auto and --auto-comment are mutually exclusive" >&2; exit 2; }
[ "$cmd" = preview ] || [ "$cmd" = submit ] || usage
[ -n "$repo" ] && [ -n "$pr" ] && [ -n "$head" ] && [ -f "$req" ] || usage
[[ "$head" =~ ^[0-9a-f]{40}$ ]] || { echo "error: --head must be the full 40-char sha" >&2; exit 2; }

# 1. validate shape
jq -e 'type=="object" and (.event|IN("COMMENT","APPROVE","REQUEST_CHANGES")) and (.body|type=="string") and (.comments|type=="array")
  and all(.comments[]; (.path|type=="string") and (.line|type=="number") and (.body|type=="string" and length>0) and ((.side // "RIGHT")|IN("LEFT","RIGHT")))' "$req" >/dev/null \
  || { echo "error: request shape invalid (event/body/comments[{path,line,side,body}])" >&2; exit 2; }
if [ "$(jq -r '.event' "$req")" = "COMMENT" ] && [ "$(jq -r '(.body|length)==0 and (.comments|length)==0' "$req")" = "true" ]; then echo "error: empty COMMENT review" >&2; exit 2; fi
# 1b. external-surface lint — local paths and bare Jira keys never leave the workspace
leaks=$(jq -r '[.body, .comments[].body] | map(select(test("(^|[^A-Za-z0-9_])\\.context/|scratchpad|/tmp/|\\.cache/tmp|(claude|ai-baton)-kit(-[0-9]+)?/|/run/user/|/Users/[a-z]|/home/[a-z]|(^|[^A-Za-z0-9_])\\.claude/"))) | length' "$req")
[ "$leaks" = 0 ] || { echo "error: $leaks body/comment(s) mention a local workspace path (.context/, scratchpad, /tmp, ai-baton-kit/ (formerly claude-kit/), home dir) — link the PR/ticket or inline the evidence instead" >&2; exit 2; }
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
      TRK_URL=$(kget tracker.url_template); rc=$?; [ $rc -eq 2 ] && exit 2
      bare=$(jq -r --arg re "$TRK_RE" '[.body, .comments[].body] | map(gsub("\\[[^\\]]*\\]\\([^)]*\\)";"") | gsub("https?://[^ )>]+";"") | select(test($re))) | length' "$req")
      [ "$bare" = 0 ] || { echo "error: $bare body/comment(s) carry a bare tracker key — every key must be a link: [KEY](${TRK_URL:-<tracker.url_template>})" >&2; exit 2; }
    fi
  fi
fi
if [ "$auto" = 1 ]; then
  [ "$(jq -r .event "$req")" = "APPROVE" ] || { echo "error: --auto is only for APPROVE" >&2; exit 2; }
  [ "$AUTO_MODE" = live ] || { echo "error: auto_approve.mode is '$AUTO_MODE', not 'live' — refusing to auto-approve (shadow mode reports only)" >&2; exit 8; }
fi
if [ "$autoc" = 1 ]; then
  # unattended auto-COMMENT path (owner-decision scope, issue-tracked): opt-in config, COMMENT only —
  # never APPROVE/REQUEST_CHANGES here, whatever the request says.
  [ "$(jq -r .event "$req")" = "COMMENT" ] || { echo "error: --auto-comment is only for COMMENT (never APPROVE/REQUEST_CHANGES on this path)" >&2; exit 2; }
  [ "$AUTOC_MODE" = live ] || { echo "error: auto_comment.mode is '$AUTOC_MODE', not 'live' — refusing to auto-comment (shadow mode reports only)" >&2; exit 8; }
fi

# 2. live PR state
live=$(gh api "repos/$repo/pulls/$pr" 2>/dev/null) || { echo "error: cannot read repos/$repo/pulls/$pr" >&2; exit 3; }
live_head=$(jq -r .head.sha <<<"$live"); base=$(jq -r .base.sha <<<"$live"); state=$(jq -r .state <<<"$live")
if [ "$autoc" = 1 ]; then
  pr_author=$(jq -r .user.login <<<"$live")
  [ "$pr_author" != "$ME" ] || { echo "error: --auto-comment refuses to post on the user's own PR" >&2; exit 8; }
fi
if [ "$state" != "open" ]; then
  # A merged/closed PR still accepts reviews (post-merge findings keep their inline anchors); allow only on explicit opt-in.
  [ "${PR_REVIEW_ALLOW_CLOSED:-0}" = "1" ] || { echo "error: PR is $state (set PR_REVIEW_ALLOW_CLOSED=1 to post a post-merge review; never APPROVE/REQUEST_CHANGES on a merged PR)" >&2; exit 3; }
  [ "$(jq -r '.event' "$req")" = "COMMENT" ] || { echo "error: PR is $state; only COMMENT reviews may be posted post-merge" >&2; exit 3; }
  echo "note: PR is $state; posting a post-merge COMMENT review" >&2
fi
[ "$live_head" = "$head" ] || { echo "error: head moved $head -> $live_head; re-run fetch-context and re-review the delta" >&2; exit 3; }
if [ "$auto" = 1 ]; then
  gate=$(python3 "$TRIVIAL" "$repo" "$pr" --head "$head" 2>&1) || { echo "error: trivial-check failed: $(jq -r '.reasons|join("; ")' <<<"$gate" 2>/dev/null || echo "$gate" | tail -c 300)" >&2; exit 8; }
  [ "$(jq -r '.eligible // false' <<<"$gate")" = true ] || { echo "error: trivial-check says NOT eligible on $head: $(jq -r '.reasons|join("; ")' <<<"$gate")" >&2; exit 8; }
  echo "note: --auto gate passed (class $(jq -r .class <<<"$gate"))" >&2
fi
# comment paths must be in the PR's file list
files_raw=$(gh api --paginate "repos/$repo/pulls/$pr/files?per_page=100" 2>&1) || { echo "error: cannot read the PR file list: $(printf '%s' "$files_raw" | tail -c 200)" >&2; exit 3; }
files=$(jq -sc '[add[]?.filename]' <<<"$files_raw") || { echo "error: PR file list is not JSON" >&2; exit 3; }
badpaths=$(jq -r --argjson f "$files" '[.comments[].path] | unique | map(select(. as $p | $f | index($p) | not)) | .[]' "$req")
[ -z "$badpaths" ] || { echo "error: comment path(s) not in PR diff: $badpaths" >&2; exit 2; }

# 3. canonical payload (marker appended once; footer stays "" in config; digest covers the exact bytes that will be posted)
marker="<!-- pr-review:v1 head=$head base=$base by=$ME -->"
payload=$(jq -c --arg m "$marker" --arg f "$FOOTER" --arg h "$head" '
  .comments |= map({path, line, side: (.side // "RIGHT"), body} + (if .start_line then {start_line, start_side: (.start_side // "RIGHT")} else {} end))
  | .body |= (rtrimstr("\n") + (if $f=="" then "" else "\n\n" + $f end) + "\n\n" + $m)
  | {commit_id: $h, event, body, comments}' "$req")
digest=$(printf '%s' "$payload" | sha256sum | cut -c1-64)

if [ "$cmd" = preview ]; then
  echo "== review preview for $repo#$pr @ ${head:0:9} (base ${base:0:9})"
  echo "event: $(jq -r .event <<<"$payload") · inline comments: $(jq '.comments|length' <<<"$payload")"
  jq -r '.comments[] | "  - \(.path):\(.line) [\(.side)] \(.body|split("\n")[0][:110])"' <<<"$payload"
  echo "-- body --"; jq -r .body <<<"$payload"; echo "-- end body --"
  echo "digest: $digest"
  echo "submit with: submit-review.sh submit --repo $repo --pr $pr --head $head --request $req --confirm $digest"
  exit 0
fi

# 4. submit
[ "$confirm" = "$digest" ] || { echo "error: --confirm does not match current digest $digest (request or head changed since preview)" >&2; exit 4; }
lock="$ROOT/.submitted/$digest"; mkdir -p "$ROOT/.submitted"
# two gates, both must hold. `mkdir "$lock"` refuses a digest dir that already exists — an earlier
# submit of this review, whether or not it left a `claimed` file. It is not a safe exclusivity check on
# every host, though: two concurrent submits can both get a yes. So the holder is whoever also creates
# $lock/claimed with the shell's own exclusive create (claim_owner, portable.sh).
{ mkdir "$lock" 2>/dev/null && claim_owner "$lock/claimed"; } || { echo "error: this exact review was already submitted (lock $lock). Inspect GitHub before retrying." >&2; exit 5; }
resp=$(printf '%s' "$payload" | gh api -X POST "repos/$repo/pulls/$pr/reviews" --input - 2>"$lock/stderr") ; rc=$?
printf '%s' "$resp" > "$lock/response.json"
if [ $rc -ne 0 ] || ! jq -e '.id' <<<"$resp" >/dev/null 2>&1; then
  echo "status: failed (rc=$rc)"; sed -n 1,20p "$lock/stderr"; jq -r '.message? // empty, (.errors[]? | tostring)' <<<"$resp" 2>/dev/null
  # a failed POST posts nothing (atomic); release the lock so a corrected request can be submitted
  rm -rf "$lock"; exit 6
fi
rid=$(jq -r .id <<<"$resp")
# 5. verify on GitHub
sleep 2
ver=$(gh api --paginate "repos/$repo/pulls/$pr/reviews?per_page=100" 2>/dev/null | jq -sc --argjson id "$rid" 'add // [] | map(select(.id==$id)) | first // null')
nposted=$(gh api --paginate "repos/$repo/pulls/$pr/comments?per_page=100" 2>/dev/null | jq -sc --argjson id "$rid" 'add // [] | map(select(.pull_request_review_id==$id)) | length')
want=$(jq '.comments|length' <<<"$payload")
if [ "$ver" = "null" ] || [ -z "$ver" ]; then echo "status: uncertain — POST returned id $rid but the review is not listed yet; re-check before any retry"; exit 7; fi
echo "status: submitted · review $rid · state $(jq -r .state <<<"$ver") · url $(jq -r .html_url <<<"$ver") · inline comments $nposted/$want"
[ "$nposted" = "$want" ] || echo "warning: inline comment count differs from request"
lstatus=reviewed; [ "$auto" = 1 ] && lstatus=auto_approved; [ "$autoc" = 1 ] && lstatus=auto_commented
( flock 9; jq -nc --arg repo "$repo" --argjson pr "$pr" --arg head "$head" --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson id "$rid" --arg ev "$(jq -r .event <<<"$payload")" --argjson n "$nposted" --arg st "$lstatus" \
  '{repo:$repo, pr:$pr, head:$head, status:$st, ts:$ts, review_id:$id, event:$ev, comments:$n}' >> "$LEDGER" ) 9>"$ROOT/.ledger.lock"
