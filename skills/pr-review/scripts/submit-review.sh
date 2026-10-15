#!/usr/bin/env bash
# submit-review.sh preview|submit --repo O/R --pr N --head SHA --request FILE [--confirm DIGEST] [--auto] [--auto-comment [--triage FILE]]
# request FILE: {"event":"COMMENT|APPROVE|REQUEST_CHANGES","body":"…","comments":[{"path":"…","line":N,"side":"RIGHT","body":"…"}]}
# preview: validates, injects the snapshot marker (no footer — owner decision 2026-09-19: attribution text only costs the reader), prints the exact payload and its digest.
# submit:  refuses unless --confirm matches the digest, the live head still equals --head, and this
#          digest has not been submitted before; posts ONE review atomically (GitHub rejects the whole
#          review with 422 if any comment cannot be anchored, so nothing half-lands); verifies afterwards.
# --auto:  the trivial-PR auto-approve path (or PR_REVIEW_AUTO=1). Refuses unless auto_approve.mode == "live" AND
#          trivial-check.py --head <sha> says eligible on the exact head being approved. Ledger status auto_approved.
# --auto-comment: the unattended auto-COMMENT path for direct review requests (or PR_REVIEW_AUTO_COMMENT=1).
#          Refuses unless the request event is "COMMENT" (or "APPROVE" on a clean sheet, below; never
#          REQUEST_CHANGES on this path), auto_comment.mode == "live", and the live PR author is not the user
#          (never the user's own PR). Ledger status auto_commented. Mutually exclusive with --auto.
#          APPROVE under --auto-comment (needs --triage FILE = the runner's triage.json, with the runner's
#          sheet.json + traps.json beside it) is allowed only when auto_comment.approve_on_clean is "live" AND
#          the clean-sheet gate holds on the exact --head (reference/runner.md § approve_on_clean has the full
#          list: the sheet bound to this head and clean, the base branch's required checks green, dismiss
#          stale reviews on, no draft/unresolved thread/CHANGES_REQUESTED, mergeable). Any miss refuses
#          (exit 8) with one line naming every reason. Ledger status auto_approved_clean.
#          "shadow": the main session posts the COMMENT exactly as "off" would, with --triage; the script
#          evaluates the same gate beside it and, once the COMMENT is posted, ledgers shadow_approve_clean
#          (would approve) or shadow_approve_refused (+ reason). An APPROVE request under shadow is refused.
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
APPROVE_CLEAN=$(jq -r '.auto_comment.approve_on_clean // "off"' "$ROOT/config.json")
TRIVIAL=$(dirname "$0")/trivial-check.py
mkdir -p "$ROOT/.submitted"; find "$ROOT/.submitted" -mindepth 1 -maxdepth 1 -type d -mtime +"$RET" -exec rm -rf {} + 2>/dev/null
usage(){ echo "usage: submit-review.sh preview|submit --repo O/R --pr N --head SHA --request FILE [--confirm DIGEST] [--auto] [--auto-comment [--triage FILE]]" >&2; exit 2; }
cmd=${1:-}; shift || usage; repo=""; pr=""; head=""; req=""; triage=""; confirm=""; auto=${PR_REVIEW_AUTO:-0}; autoc=${PR_REVIEW_AUTO_COMMENT:-0}
while [ $# -gt 0 ]; do case $1 in
  --repo) repo=$2; shift 2;; --pr) pr=$2; shift 2;; --head) head=$2; shift 2;; --request) req=$2; shift 2;; --confirm) confirm=$2; shift 2;; --auto) auto=1; shift;; --auto-comment) autoc=1; shift;; --triage) triage=$2; shift 2;; *) usage;; esac; done
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
approve_clean=0; shadow_eval=0
if [ "$autoc" = 1 ]; then
  # unattended auto-COMMENT path (owner-decision scope, issue-tracked): opt-in config. COMMENT, or APPROVE
  # on a clean runner sheet under auto_comment.approve_on_clean — never REQUEST_CHANGES here, whatever the
  # request says.
  case "$(jq -r .event "$req")" in
    COMMENT) [ -z "$triage" ] || shadow_eval=1 ;;
    APPROVE) approve_clean=1 ;;
    *) echo "error: --auto-comment is only for COMMENT (or APPROVE on a clean sheet under auto_comment.approve_on_clean); never REQUEST_CHANGES on this path" >&2; exit 2 ;;
  esac
  [ "$AUTOC_MODE" = live ] || { echo "error: auto_comment.mode is '$AUTOC_MODE', not 'live' — refusing to auto-comment (shadow mode reports only)" >&2; exit 8; }
  if [ "$approve_clean" = 1 ] && [ "$APPROVE_CLEAN" != live ]; then
    echo "error: auto_comment.approve_on_clean is '$APPROVE_CLEAN', not 'live' — refusing to auto-approve a clean sheet (shadow posts the COMMENT with --triage and only ledgers the would-approve)" >&2; exit 8
  fi
  if [ "$shadow_eval" = 1 ] && [ "$APPROVE_CLEAN" != shadow ]; then
    echo "error: --triage on a COMMENT is the approve_on_clean shadow evaluation, and approve_on_clean is '$APPROVE_CLEAN', not 'shadow'" >&2; exit 2
  fi
  if [ "$approve_clean" = 1 ] || [ "$shadow_eval" = 1 ]; then
    [ -n "$triage" ] && [ -f "$triage" ] || { echo "error: an APPROVE under --auto-comment needs --triage FILE (the runner's triage.json, sheet.json + traps.json beside it) to prove the sheet is clean" >&2; exit 2; }
  fi
fi
[ "$approve_clean" = 1 ] || [ "$shadow_eval" = 1 ] || [ -z "$triage" ] || { echo "error: --triage only applies under --auto-comment (an APPROVE, or the approve_on_clean shadow COMMENT)" >&2; exit 2; }

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
clean_gate(){ # fills why[] (refusals) and snote[] (shadow-only notes) for the clean-sheet gate on $head
  local dir sheet traps v nu br prot rules perr prc protected base_ref ms draft required dismiss need_appr approvals
  local crs sts ncr nst badcr reqbad stbad crq th unres E='{}' A='[]'
  dir=$(dirname "$triage"); sheet="$dir/sheet.json"; traps="$dir/traps.json"
  # (a) the runner's sheet, bound to this exact head by its sidecar (a triage.json alone carries no head)
  if ! jq -e 'type=="array"' "$triage" >/dev/null 2>&1; then why+=("triage file is not a findings array")
  else v=$(jq 'length' "$triage"); [ "$v" = 0 ] || why+=("triage has $v finding(s)"); fi
  if [ ! -f "$sheet" ]; then why+=("no sheet.json beside the triage (the runner's sidecar binding it to a head)")
  elif ! jq -e 'type=="object"' "$sheet" >/dev/null 2>&1; then why+=("sheet.json is not an object")
  else
    [ "$(jq -r --arg r "$repo" --arg p "$pr" '(.repo==$r) and ((.pr|tostring)==$p)' "$sheet")" = true ] || why+=("sheet.json is for $(jq -r '"\(.repo)#\(.pr)"' "$sheet"), not $repo#$pr")
    v=$(jq -r '.head // ""' "$sheet"); [ "$v" = "$head" ] || why+=("sheet.json is for head ${v:0:9}, not ${head:0:9} (stale sheet)")
    v=$(jq -r '.recommend // ""' "$sheet"); [ "$v" = "APPROVE-if-user-agrees" ] || why+=("sheet recommends '$v', not APPROVE-if-user-agrees")
    v=$(jq -r '.needs // ""' "$sheet"); [ "$v" = nothing ] || why+=("sheet NEEDS '$v', not 'nothing'")
    v=$(jq -r '.unverified_traps // "missing"' "$sheet"); [ "$v" = 0 ] || why+=("sheet unverified_traps is $v")
    v=$(jq -r '.demoted // "missing"' "$sheet"); [ "$v" = 0 ] || why+=("sheet demoted is $v (lens claims turned into body questions)")
  fi
  if [ ! -f "$traps" ]; then why+=("no traps.json beside the triage")
  elif ! nu=$(jq 'if type=="array" then [.[] | select(.result=="unverified")] | length else error("not a list") end' "$traps" 2>/dev/null); then why+=("traps.json is not a trap list")
  else [ "$nu" = 0 ] || why+=("traps.json has $nu unverified trap(s)"); fi
  [ "$(jq -r '.comments|length' "$req")" = 0 ] || why+=("request carries inline comments")
  # (b) the live PR: not a draft, nothing unresolved, mergeable
  draft=$(jq -r '.draft // false' <<<"$live"); [ "$draft" = false ] || why+=("PR is a draft")
  ms=$(jq -r '.mergeable_state // "unknown"' <<<"$live"); base_ref=$(jq -r '.base.ref // ""' <<<"$live")
  th=$(gh api graphql --paginate -f query='query($o:String!,$r:String!,$n:Int!,$endCursor:String){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100,after:$endCursor){pageInfo{hasNextPage endCursor}nodes{isResolved}}}}}' \
    -F o="${repo%/*}" -F r="${repo#*/}" -F n="$pr" 2>/dev/null) || th=""
  if ! unres=$(jq -se 'if all(.[]; .data.repository.pullRequest.reviewThreads.nodes|type=="array") then [.[].data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved|not)] | length else error("no threads") end' <<<"$th" 2>/dev/null); then
    why+=("cannot read the review threads")
  elif [ "$unres" != 0 ]; then why+=("$unres unresolved review thread(s)"); fi
  # (c) the base branch's gate: classic protection (admin read) + rulesets (read access) + the branch summary
  br=""; prot=""; rules=""
  if [ -z "$base_ref" ]; then why+=("live PR has no base ref")
  else
    br=$(gh api "repos/$repo/branches/$base_ref" 2>/dev/null) || { br=""; why+=("cannot read branch $base_ref"); }
    protected=$(jq -r '.protected // false' <<<"${br:-$E}" 2>/dev/null)
    perr=$(mktemp); prot=$(gh api "repos/$repo/branches/$base_ref/protection" 2>"$perr"); prc=$?
    if [ $prc -ne 0 ]; then
      if grep -q 'HTTP 404' "$perr" && [ "$protected" = false ]; then prot=$E   # no classic protection on this branch
      elif grep -qE 'HTTP 40[34]' "$perr"; then prot=""; why+=("branch protection on $base_ref is unreadable ($(grep -oE 'HTTP 40[34]' "$perr" | head -1)) — an unreadable gate is not a green gate")
      else prot=""; why+=("cannot read branch protection on $base_ref"); fi
    fi
    rm -f "$perr"
    rules=$(gh api --paginate "repos/$repo/rules/branches/$base_ref?per_page=100" 2>/dev/null) && rules=$(jq -sc 'add // []' <<<"$rules" 2>/dev/null) || { rules=""; why+=("cannot read the rulesets on $base_ref"); }
  fi
  required=$(jq -nc --argjson b "${br:-$E}" --argjson p "${prot:-$E}" --argjson r "${rules:-$A}" '[($b.protection.required_status_checks.contexts // [])[],
    ($p.required_status_checks.contexts // [])[], ($p.required_status_checks.checks // [])[].context,
    ($r[] | select(.type=="required_status_checks") | .parameters.required_status_checks[]?.context)] | unique')
  dismiss=$(jq -n --argjson p "${prot:-$E}" --argjson r "${rules:-$A}" '($p.required_pull_request_reviews.dismiss_stale_reviews // false)
    or any($r[]; .type=="pull_request" and (.parameters.dismiss_stale_reviews_on_push // false))')
  need_appr=$(jq -n --argjson p "${prot:-$E}" --argjson r "${rules:-$A}" '[($p.required_pull_request_reviews.required_approving_review_count // 0),
    ($r[] | select(.type=="pull_request") | .parameters.required_approving_review_count // 0)] | max')
  # (d) checks on the exact head: every required context has a completed `success`; skipped/neutral is
  #     green only for a check the base branch does not require
  cr_raw=$(gh api --paginate "repos/$repo/commits/$head/check-runs?per_page=100" 2>/dev/null) || { cr_raw=""; why+=("cannot read check-runs on head"); }
  st_raw=$(gh api "repos/$repo/commits/$head/status" 2>/dev/null) || { st_raw=""; why+=("cannot read combined status on head"); }
  crs=$(jq -sc '[.[].check_runs[]?]' <<<"${cr_raw:-$E}" 2>/dev/null) || { crs=$A; why+=("check-runs unparseable"); }
  sts=$(jq -c '[.statuses[]?]' <<<"${st_raw:-$E}" 2>/dev/null) || { sts=$A; why+=("combined status unparseable"); }
  ncr=$(jq 'length' <<<"$crs"); nst=$(jq 'length' <<<"$sts")
  if [ -n "$cr_raw" ] && [ "$ncr" = 0 ] && [ "$nst" = 0 ]; then why+=("no check-runs or statuses on head"); fi
  badcr=$(jq -r --argjson req "$required" '[.[] | select((.name as $n | $req | index($n)) | not)
    | select(.status != "completed" or ((.conclusion // "") | IN("success","skipped","neutral") | not)) | .name] | join(", ")' <<<"$crs")
  [ -z "$badcr" ] || why+=("checks not green: $badcr")
  reqbad=$(jq -rn --argjson req "$required" --argjson cr "$crs" --argjson st "$sts" '[$req[] as $c
    | [$cr[] | select(.name==$c)] as $runs | [$st[] | select(.context==$c)] as $ss
    | if any($runs[]; .status=="completed" and .conclusion=="success") or any($ss[]; .state=="success") then empty
      elif ($runs|length)==0 and ($ss|length)==0 then "\($c) not reported yet"
      else "\($c) \(([$runs[] | .conclusion // .status] + [$ss[].state]) | join("/"))" end] | join(", ")')
  [ -z "$reqbad" ] || why+=("required checks not green: $reqbad")
  if [ -n "$st_raw" ]; then
    stbad=$(jq -r 'if (.total_count // 0) > 0 and .state != "success" then .state else "" end' <<<"$st_raw" 2>/dev/null) || stbad="unparseable"
    [ -z "$stbad" ] || why+=("combined status $stbad")
  fi
  # (e) reviews: no standing CHANGES_REQUESTED; the approvals count tells a `blocked` state apart
  approvals=0
  rev_raw=$(gh api --paginate "repos/$repo/pulls/$pr/reviews?per_page=100" 2>/dev/null) || { rev_raw=""; why+=("cannot read the PR reviews"); }
  if [ -n "$rev_raw" ]; then
    crq=$(jq -rs '[.[][]? | select(.state | IN("APPROVED","CHANGES_REQUESTED","DISMISSED"))] | group_by(.user.login) | map(last | select(.state=="CHANGES_REQUESTED") | .user.login) | join(", ")' <<<"$rev_raw" 2>/dev/null) || crq="unparseable"
    [ -z "$crq" ] || why+=("CHANGES_REQUESTED standing from: $crq")
    approvals=$(jq -s --arg a "$pr_author" '[.[][]? | select(.user.login != $a and (.state | IN("APPROVED","CHANGES_REQUESTED","DISMISSED")))] | group_by(.user.login) | map(last | select(.state=="APPROVED")) | length' <<<"$rev_raw" 2>/dev/null) || approvals=0
  fi
  # (f) mergeable_state: clean, or blocked only where the missing approval explains it (required approvals
  #     > approvals present; every other gate this function reads already held or refused above)
  case "$ms" in
    clean) ;;
    blocked)
      if [ "${need_appr:-0}" -gt 0 ] 2>/dev/null && [ "$approvals" -lt "$need_appr" ]; then :
      else why+=("mergeable_state blocked, and not by a missing approval ($approvals of ${need_appr:-?} required present) — cannot tell what blocks it"); fi ;;
    *) why+=("mergeable_state $ms (needs clean, or blocked only by the missing approval)") ;;
  esac
  # (g) an APPROVE must not outlive a later push: dismiss stale reviews on the base branch (classic or ruleset)
  if [ "$dismiss" != true ] && [ -n "$base_ref" ]; then
    v="dismiss stale reviews is off on $base_ref, so an APPROVE would survive later pushes — enable dismiss stale reviews on $base_ref, or keep approve_on_clean: shadow"
    if [ "$approve_clean" = 1 ]; then why+=("$v"); else snote+=("live would also refuse: $v"); fi
  fi
}
shadow_verdict=""; shadow_pass=0
if [ "$approve_clean" = 1 ] || [ "$shadow_eval" = 1 ]; then
  # clean-sheet gate, re-read at call time on the exact head — the main session's decision is never trusted alone
  why=(); snote=(); clean_gate
  reasons=""; [ ${#why[@]} -gt 0 ] && { reasons=$(printf '%s; ' "${why[@]}"); reasons=${reasons%; }; }
  if [ "$approve_clean" = 1 ]; then
    [ -z "$reasons" ] || { echo "error: approve_on_clean gate refused on ${head:0:9}: $reasons" >&2; exit 8; }
    echo "note: approve_on_clean gate passed on ${head:0:9} (clean sheet on this head, required checks green, dismiss stale reviews on, nothing unresolved, author is not the user)" >&2
  else
    if [ -z "$reasons" ]; then shadow_pass=1; shadow_verdict="would approve (clean)"; else shadow_verdict="would refuse: $reasons"; fi
    [ ${#snote[@]} -gt 0 ] && shadow_verdict="$shadow_verdict ($(printf '%s; ' "${snote[@]}" | sed 's/; $//'))"
    echo "note: approve_on_clean shadow on ${head:0:9}: $shadow_verdict" >&2
  fi
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
  [ "$shadow_eval" = 1 ] && echo "note: approve_on_clean is shadow — submit posts this COMMENT, then ledgers the gate's verdict: $shadow_verdict"
  echo "submit with: submit-review.sh submit --repo $repo --pr $pr --head $head --request $req --confirm $digest"
  exit 0
fi

# 4. submit
[ "$confirm" = "$digest" ] || { echo "error: --confirm does not match current digest $digest (request or head changed since preview)" >&2; exit 4; }
if [ "$approve_clean" = 1 ]; then
  # narrow the gate→POST race: re-read the head right before the POST (the payload pins commit_id to it too)
  now_head=$(gh api "repos/$repo/pulls/$pr" 2>/dev/null | jq -r '.head.sha // ""' 2>/dev/null)
  [ "$now_head" = "$head" ] || { echo "error: head moved ${head:0:9} -> ${now_head:0:9} between the gate and the POST; nothing posted" >&2; exit 3; }
fi
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
lstatus=reviewed; [ "$auto" = 1 ] && lstatus=auto_approved; [ "$autoc" = 1 ] && lstatus=auto_commented; [ "$approve_clean" = 1 ] && lstatus=auto_approved_clean
( flock 9; jq -nc --arg repo "$repo" --argjson pr "$pr" --arg head "$head" --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson id "$rid" --arg ev "$(jq -r .event <<<"$payload")" --argjson n "$nposted" --arg st "$lstatus" \
  '{repo:$repo, pr:$pr, head:$head, status:$st, ts:$ts, review_id:$id, event:$ev, comments:$n}' >> "$LEDGER"
  # shadow approve_on_clean: the gate's verdict rides beside the COMMENT just posted (never instead of it)
  if [ "$shadow_eval" = 1 ]; then
    sst=shadow_approve_refused; [ "$shadow_pass" = 1 ] && sst=shadow_approve_clean
    jq -nc --arg repo "$repo" --argjson pr "$pr" --arg head "$head" --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson id "$rid" --arg st "$sst" --arg why "$shadow_verdict" \
      '{repo:$repo, pr:$pr, head:$head, status:$st, ts:$ts, comment_review_id:$id, event:"APPROVE", reason:$why}' >> "$LEDGER"
  fi ) 9>"$ROOT/.ledger.lock"; lrc=$?
[ "$shadow_eval" = 1 ] && echo "shadow: $shadow_verdict"
exit $lrc
