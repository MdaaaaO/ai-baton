#!/usr/bin/env bash
# pr-watch.sh <owner/repo> <pr> <head_sha_prefix> [<pr> <head_sha_prefix> ...] — low-noise PR watch (any repo).
#   ONE process watches N PRs of the same repo (round-robin, one `sleep 120` per cycle), so a session arms ONE
#   Monitor for all its PRs instead of one per PR (owner decision 2026-09-22: polling is free, but every emitted line and
#   every Monitor expiry wakes the model at a full prefix). The 3-arg single-PR form is unchanged.
#   Per-PR state (head, seen ids, sync cooldown, …) lives in ${TMPDIR:-/tmp}/pr-watch-<owner-repo>-<pr>/, one small
#   file per variable — POSIX sh has no arrays. Every emitted line keeps the prefix "PR <n> <EVENT>…" (the harness
#   and the pr-event-brief skill parse it). A merged/closed PR is dropped from the round-robin; the process exits
#   when every watched PR is gone.
# Emits ONLY actionable events:
#   bot Assessment on <head>; new top-level review comments from anyone but this login and
#   the bot's in-thread replies; new reviews (approve/changes/comment) from anyone but this login;
#   new issue comments from anyone but this login; non-green checks; head moved (not by its own sync);
#   merged/closed (exit). Own replies and bot "thanks" replies are filtered out.
#   Auto-sync (owner decision 2026-09-21): while the PR waits for review, keep its branch merged with the base branch —
#   when it is behind, no human APPROVED review exists and the cooldown has passed, PUT pulls/N/update-branch
#   (GitHub-signed merge commit). Emits SYNCED / BEHIND (approved, 403, 422) / CONFLICTS lines. PR_WATCH_SYNC=0 disables,
#   PR_WATCH_SYNC_COOLDOWN=<s> (default 3600) limits how often a fast-moving base re-triggers CI on the PR.
#   The merge commit such a sync lands is tracked silently (SYNCED already said it; no HEAD MOVED follows), and in bot
#   mode the watcher then removes and re-adds the review bot as a requested reviewer itself (RE-REQUEST line only if
#   that fails) — each was a wake-up that asked the session for nothing it could not do here.
#   CHECK NOT GREEN (owner decision 2026-09-22): at most ONE line per head (reset on HEAD MOVED), and only once the suite
#   has settled (no check run queued/in_progress), listing every failing check at that moment — previously one line
#   per newly-finished failing check (~10 wake-ups for one known cause).
#   PR_WATCH_KNOWN_RED=<extended-regex>: before emitting, fetch the failure-level annotations of the failing check
#   runs; if every failing check run has at least one failure annotation and ALL of them match the regex, the line
#   is suppressed (logged to stderr only). A failing check run with zero annotations is unexplained → line emitted.
#   PR_WATCH_REPLAY=1: re-emit the current bot verdict / CHECK NOT GREEN on start even when the state dir already
#   holds this head (default: a re-arm on a known head is silent about what it already reported; the state dir is
#   ${TMPDIR:-/tmp}/pr-watch-<owner>-<repo>-<pr>/ and is shared by every session on the same machine).
KIT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=../_lib/portable.sh
. "$KIT/skills/_lib/portable.sh"  # iso_to_epoch — GNU/Linux and macOS/BSD alike
# The review bot's login comes from the env config (github.review_bot); PR_WATCH_BOT_LOGIN overrides (skips the
# lookup). `kit_profile.py get` exits 1 when the key is ABSENT (a real failure) and exits 0 printing an empty
# line when the value is merely "" — capture the exit status separately: a failed lookup is an error (it would
# silently open the no-bot branch's lighter gate), never read the same as "no bot configured".
if [ -n "${PR_WATCH_BOT_LOGIN:-}" ]; then
  bot=$PR_WATCH_BOT_LOGIN
else
  boterr=$(mktemp)
  bot=$(python3 "$KIT/context-db/bin/kit_profile.py" get github.review_bot 2>"$boterr"); botrc=$?
  if [ $botrc -ne 0 ]; then msg=$(head -1 "$boterr" | tr -d '\r'); echo "ERROR $1 startup: kit_profile.py get github.review_bot: ${msg:-key absent from the env store — run kb.py config-set github.review_bot '' (or the bot login)}"; rm -f "$boterr"; exit 1; fi
  rm -f "$boterr"
fi
# The bot logins to exclude from a "human approval" count (github.bots — gh-cli SKILL.md: "never hardcode
# either"). Same absent-vs-empty distinction as github.review_bot above: `get` exits 1 when the key itself is
# missing (a real failure — never silently fall back to excluding nothing), 0 with "[]" when it is configured
# empty. Printed as JSON (a list), read back with jq --argjson below.
botserr=$(mktemp)
bots=$(python3 "$KIT/context-db/bin/kit_profile.py" get github.bots 2>"$botserr"); botsrc=$?
if [ $botsrc -ne 0 ]; then msg=$(head -1 "$botserr" | tr -d '\r'); echo "ERROR $1 startup: kit_profile.py get github.bots: ${msg:-key absent from the env store — run kb.py config-set github.bots '[]' (or the bot logins)}"; rm -f "$botserr"; exit 1; fi
rm -f "$botserr"
if [ -n "$bot" ]; then echo "pr-watch: bot mode — polling $bot Assessment + CI + human review" >&2
else echo "pr-watch: no-bot mode (github.review_bot empty) — gating on CI + human review only" >&2; fi
eval "$(python3 "$KIT/context-db/bin/kit_profile.py" gh-env)"  # github.sandbox_token_prefix, if any
eval "$(python3 "$KIT/context-db/bin/kit_profile.py" identity-env)"  # WORKSPACE_* from plugin userConfig, if set
# Identity must be known: with it wrong (or falling back to a placeholder), the filters below that drop
# "own" comments/reviews never match, and every reply this session posted comes back as a NEW event forever.
if [ -n "${PR_WATCH_SELF:-}" ]; then
  me=$PR_WATCH_SELF
elif [ -n "${WORKSPACE_GITHUB_LOGIN:-}" ]; then
  me=$WORKSPACE_GITHUB_LOGIN
else
  meerr=$(mktemp)
  me=$(gh api user --jq .login 2>"$meerr"); mrc=$?
  if [ $mrc -ne 0 ] || [ -z "$me" ]; then
    echo "ERROR $1 startup: cannot resolve this session's GitHub identity ($(head -1 "$meerr" | tr -d '\r')) — set PR_WATCH_SELF or WORKSPACE_GITHUB_LOGIN"
    rm -f "$meerr"; exit 2
  fi
  rm -f "$meerr"
fi
sync=${PR_WATCH_SYNC:-1}; sync_cool=${PR_WATCH_SYNC_COOLDOWN:-3600}; known_red=${PR_WATCH_KNOWN_RED:-}
usage() { echo "usage: pr-watch.sh <owner/repo> <pr_number> <head_sha_prefix> [<pr_number> <head_sha_prefix> ...]" >&2; exit 2; }
repo=$1; [ -z "$repo" ] && usage; shift
[ $# -lt 2 ] && usage; [ $(( $# % 2 )) -ne 0 ] && usage
base_dir="${TMPDIR:-/tmp}/pr-watch-$(printf %s "$repo" | tr / -)"
getv() { cat "$D/$1" 2>/dev/null; }                      # per-PR state: $D is set per round-robin step
putv() { printf '%s\n' "$2" >"$D/$1"; }
# Register every watched PR. The state dir survives a re-arm: when it already holds this head, the event state
# (bot verdict, CHECK NOT GREEN, seen ids) is kept so a re-armed watcher stays SILENT about what it already
# reported and only emits what changed in the gap (2026-09-22 — every replayed line was a wasted ~$0.30 wake-up).
# The state is reset — old replay behaviour — when the head differs, no state exists, or PR_WATCH_REPLAY=1.
# A head argument that differs from the state is NOT a reason to reset when the state's head is the PR's live head:
# the watcher already followed that move (a HEAD MOVED it reported, or its own update-branch it tracked silently),
# and the argument is just the original arming command's head, reused verbatim on an expiry re-arm. Resetting there
# re-emitted HEAD MOVED and replayed the bot verdict — a wasted wake-up per re-arm.
prs=""
while [ $# -gt 0 ]; do
  pr=$1; h=$2; shift 2; D="$base_dir-$pr"; mkdir -p "$D" || exit 2
  seed=$(gh api "repos/$repo/pulls/$pr" --jq '.head.sha' 2>/dev/null)
  prev=$(getv head); same=0
  case "$prev" in "") ;; "$h"*) same=1;; *) case "$h" in "$prev"*) same=1;; esac;; esac
  if [ "$same" = 0 ] && [ -n "$prev" ] && [ -n "$seed" ]; then case "$seed" in "$prev"*) same=1;; esac; fi
  if [ "$same" = 1 ] && [ "${PR_WATCH_REPLAY:-0}" != 1 ] && [ -s "$D/init" ]; then
    echo "pr-watch: PR $pr re-armed on known head $prev — silent about already-reported state (PR_WATCH_REPLAY=1 to replay)" >&2
  else
    rm -f "$D/seen_bot" "$D/seen_c" "$D/seen_r" "$D/seen_i" "$D/init" "$D/notgreen" "$D/sync_stuck" "$D/appr_seen" "$D/dirty_seen" "$D/sync_from"
    : >"$D/seen_c"; : >"$D/seen_r"; : >"$D/seen_i"; putv head "$h"; putv seen_bot 0
  fi
  # Seed the cooldown from the current head: if it is a GitHub-made merge commit (update-branch), its
  # committer date is the last sync — a re-armed watcher must not restart the clock at 0.
  [ -f "$D/last_sync" ] || putv last_sync 0
  if [ -n "$seed" ]; then
    seed_info=$(gh api "repos/$repo/commits/$seed" --jq '"\(.parents|length) \(.committer.login // "") \(.commit.committer.date)"' 2>/dev/null)
    case "$seed_info" in
      2\ web-flow\ *) putv last_sync "$(iso_to_epoch "${seed_info##* }" 2>/dev/null || echo 0)" ;;
    esac
  fi
  prs="$prs $pr"
done
while true; do
  alive=""
  for pr in $prs; do
    D="$base_dir-$pr"; head=$(getv head); init=$(getv init)
    ierr=$(mktemp)
    info=$(gh api "repos/$repo/pulls/$pr" --jq '"\(.head.sha) \(.base.ref) \(.mergeable_state)"' 2>"$ierr"); irc=$?
    if [ $irc -ne 0 ] || [ -z "$info" ]; then
      # a failed fetch is never "no change" — surface it and keep watching next cycle instead of going silent
      echo "ERROR $repo#$pr $(head -1 "$ierr" | tr -d '\r')"; rm -f "$ierr"; alive="$alive $pr"; continue
    fi
    rm -f "$ierr"
    cur=${info%% *}; rest=${info#* }; base=${rest%% *}; mstate=${rest##* }
    case "$cur" in "$head"*) ;; *)
      short=$(printf %s "$cur" | cut -c1-9)
      # This watcher's own update-branch (sync_from = the head it synced FROM) is not news to the session — it
      # already got the SYNCED line. Recognised strictly: the new head is a two-parent GitHub (web-flow) merge commit
      # whose first parent is exactly that head. Anything else — a push, someone else's rebase, a lookup failure —
      # is a real HEAD MOVED, as before.
      self=0; from=$(getv sync_from)
      if [ -n "$from" ]; then
        pinfo=$(gh api "repos/$repo/commits/$cur" --jq '"\(.parents|length) \(.parents[0].sha // "") \(.committer.login // "")"' 2>/dev/null)
        [ "$pinfo" = "2 $from web-flow" ] && self=1
      fi
      rm -f "$D/sync_from"
      if [ "$self" = 1 ]; then echo "pr-watch: PR $pr head $head -> $short is this watcher's own update-branch merge — tracked silently" >&2
      else echo "PR $pr HEAD MOVED to $short (was $head)"; fi
      head=$short; putv head "$head"; putv seen_bot 0; rm -f "$D/notgreen"
      # The review bot does not re-review a merge-commit head on its own, and a plain POST re-request is a no-op there
      # (GitHub thinks it already asked) — remove, then re-add, as pr-merge.sh's force_review does. Only now, once the
      # sync commit is the head: a request sent before it landed would review a head that is about to be replaced.
      # A failure is a line (the session must re-request by hand), never silence.
      if [ "$self" = 1 ] && [ -n "$bot" ]; then
        if gh api -X DELETE "repos/$repo/pulls/$pr/requested_reviewers" -f "reviewers[]=$bot" >/dev/null 2>"$D/.rrerr" &&
           gh api -X POST "repos/$repo/pulls/$pr/requested_reviewers" -f "reviewers[]=$bot" >/dev/null 2>"$D/.rrerr"; then
          echo "pr-watch: PR $pr re-requested $bot on $short (remove + re-add)" >&2
        else
          echo "PR $pr RE-REQUEST of $bot on $short failed after the auto-sync: $(head -1 "$D/.rrerr" | tr -d '\r') — re-request by hand (DELETE then POST requested_reviewers)"
        fi
        rm -f "$D/.rrerr"
      fi;;
    esac
    # keep the branch merged with its base while we wait for reviews (we cannot merge anyway, so a stale branch only delays the merge)
    if [ "$sync" = 1 ] && [ -n "$cur" ] && [ -n "$base" ] && [ "$(getv sync_stuck)" != "$cur" ]; then
      if [ "$mstate" = dirty ]; then
        if [ "$(getv dirty_seen)" != "$cur" ]; then echo "PR $pr CONFLICTS with $base — rebase the worktree and enqueue sign-queue --rebase; auto-sync skipped"; putv dirty_seen "$cur"; fi
      else
        behind=$(gh api "repos/$repo/compare/$base...$cur" --jq .behind_by 2>/dev/null)
        if [ "${behind:-0}" -gt 0 ] 2>/dev/null; then
          now=$(date +%s); last_sync=$(getv last_sync)
          if [ $((now - ${last_sync:-0})) -ge "$sync_cool" ]; then
            araw=$(gh api --paginate "repos/$repo/pulls/$pr/reviews?per_page=100" 2>"$D/.aerr"); arc=$?
            if [ $arc -ne 0 ]; then
              # a failed fetch is never "0 approvals" — that would sync (and dismiss) an actually-approved PR;
              # report it and skip the sync attempt this cycle, retry next cycle instead
              echo "ERROR $repo#$pr $(head -1 "$D/.aerr" | tr -d '\r')"; rm -f "$D/.aerr"
            else
              rm -f "$D/.aerr"
              # Neither the configured review bot's own approval nor a login in the configured `github.bots`
              # list (which auto-merge.yml's REVIEWER — the identity its own approval carries, a different
              # login than github.review_bot when the bot posts its Assessment under its own account —
              # defaults into; never hardcode that login here, gh-cli SKILL.md) counts as the human approval
              # that would make a push dismiss something worth keeping: without this exclusion a PR approved
              # only by one of those never gets synced while it sits BEHIND, and auto-merge — which requires a
              # clean, non-BEHIND head — never runs.
              appr=$(printf '%s' "$araw" | jq -s --arg bot "$bot" --argjson bots "$bots" '[.[] | .[] | select(.state=="APPROVED" and .user.login!=$bot and ((.user.login as $l | ($bots | index($l))) == null))] | length')  # gh api --jq has no --arg (gh-cli skill)
              if [ "${appr:-0}" -gt 0 ] 2>/dev/null; then
                if [ "$(getv appr_seen)" != "$cur" ]; then echo "PR $pr BEHIND $base by $behind but APPROVED — not auto-syncing (a push would dismiss the approval where dismiss_stale_reviews is on): merge now, or update-branch and ask for re-approval"; putv appr_seen "$cur"; fi
              else
                out=$(gh api -X PUT "repos/$repo/pulls/$pr/update-branch" -f expected_head_sha="$cur" 2>&1); rc=$?
                if [ "$rc" = 0 ]; then
                  if [ -n "$bot" ]; then rr="the watcher re-requests $bot itself once it lands"; else rr="nothing to re-request"; fi
                  echo "PR $pr SYNCED with $base (was $behind behind): update-branch requested on $(printf %s "$cur" | cut -c1-9) — the merge head is tracked silently (no HEAD MOVED follows) and $rr; further worktree pushes need --rebase"
                  putv last_sync "$now"; putv sync_from "$cur"
                else
                  case "$out" in
                    *"HTTP 403"*) echo "PR $pr BEHIND $base by $behind — update-branch refused (403: the token lacks the workflow scope, PR touches .github/workflows?) — rebase the worktree + sign-queue --rebase, or on the user's machine: gh pr update-branch $pr";;
                    *"HTTP 422"*) echo "PR $pr BEHIND $base by $behind — update-branch 422 (merge conflict or head moved): $(printf %s "$out" | tr '\n' ' ' | cut -c1-200)";;
                    *) echo "PR $pr BEHIND $base by $behind — update-branch failed: $(printf %s "$out" | tr '\n' ' ' | cut -c1-200)";;
                  esac
                  putv sync_stuck "$cur"
                fi
              fi
            fi
          fi
        fi
      fi
    fi
    if [ -n "$bot" ] && [ "$(getv seen_bot)" = 0 ]; then
      verr=$(mktemp)
      v=$(PR_WATCH_BOT_LOGIN=$bot bash "$KIT/skills/pr-watch/bot-verdict.sh" "$repo" "$pr" "$head" 2>"$verr"); vrc=$?
      case $vrc in
        0) [ "$v" = none ] || { putv seen_bot 1; echo "PR $pr BOT REVIEW on $head: $v"; } ;;
        2) : ;;                                            # bot became unconfigured mid-run — nothing to report
        *) echo "ERROR $repo#$pr $(head -1 "$verr" | tr -d '\r')" ;;
      esac
      rm -f "$verr"
    fi
    # Checks: one line per head, only once nothing is still running, listing every failing check.
    # statusCheckRollup mixes two node shapes: a CheckRun (status/conclusion/name) and a legacy commit status,
    # a StatusContext (state/context only — no status or conclusion field at all). Reading status/conclusion off
    # every node without telling them apart drops every StatusContext silently: it never counts as pending and
    # never counts as bad, so a PR whose only red signal is an external CI status (a required status context)
    # gets reported green. Map state to the same tri-state conclusion/status pair a CheckRun carries instead.
    if [ -n "$cur" ] && [ "$(getv notgreen)" != "$cur" ]; then
      roll=$(gh pr view "$pr" --repo "$repo" --json statusCheckRollup --jq '
        .statusCheckRollup[] |
        if .__typename == "StatusContext" then
          (if (.state=="PENDING" or .state=="EXPECTED") then "IN_PROGRESS" else "COMPLETED" end) as $status |
          (if .state=="SUCCESS" then "SUCCESS" elif (.state=="PENDING" or .state=="EXPECTED") then "" else "FAILURE" end) as $concl |
          "\($status)\t\($concl)\t\(.context // "")"
        else
          "\(.status // "")\t\(.conclusion // "")\t\(.name // "")"
        end' 2>/dev/null)
      pend=$(printf '%s\n' "$roll" | awk -F'\t' '$1=="QUEUED"||$1=="IN_PROGRESS"||$1=="PENDING"||$1=="WAITING"||$1=="REQUESTED"' | grep -c . )
      bad=$(printf '%s\n' "$roll" | awk -F'\t' '$1=="COMPLETED" && $2!="" && $2!="SUCCESS" && $2!="SKIPPED" && $2!="NEUTRAL" {print $3": "$2}' | sort -u | tr '\n' ';')
      if [ -n "$bad" ] && [ "$pend" = 0 ]; then
        mute=0
        if [ -n "$known_red" ]; then
          mute=1
          runs=$(gh api --paginate "repos/$repo/commits/$cur/check-runs?per_page=100" --jq '.check_runs[] | select(.status=="completed" and .conclusion!=null and .conclusion!="success" and .conclusion!="skipped" and .conclusion!="neutral") | .id' 2>/dev/null)
          [ -z "$runs" ] && mute=0                        # no failing check RUN behind the failing rollup entry → unexplained
          for id in $runs; do
            ann=$(gh api --paginate "repos/$repo/check-runs/$id/annotations?per_page=100" --jq '.[] | select(.annotation_level=="failure") | [(.path//""),(.title//""),(.message//"")] | map(gsub("\n";" ")) | join(" ")' 2>/dev/null)
            [ -z "$ann" ] && { mute=0; break; }            # a failing check with no annotation is unexplained
            printf '%s\n' "$ann" | grep -Ev "$known_red" | grep -q . && { mute=0; break; }
          done
        fi
        if [ "$mute" = 1 ]; then echo "pr-watch: PR $pr CHECK NOT GREEN muted by PR_WATCH_KNOWN_RED ($bad)" >&2
        else echo "PR $pr CHECK NOT GREEN: $bad"; fi
        putv notgreen "$cur"
      fi
    fi
    new=""
    # review comments: skip own, skip bot in-thread replies. A failed page (rate limit, a transient 5xx) must
    # never read the same as "no new comments" — that silently drops whatever page failed, possibly for good
    # (the ids on it are never retried once the state dir has moved past them); report it and skip this listing
    # for the cycle instead.
    cerr=$(mktemp)
    craw=$(gh api --paginate "repos/$repo/pulls/$pr/comments?per_page=100" --jq '.[] | "\(.id):\(.user.login):\(if .in_reply_to_id then "reply" else "top" end)"' 2>"$cerr"); crc=$?
    if [ $crc -ne 0 ]; then echo "ERROR $repo#$pr $(head -1 "$cerr" | tr -d '\r')"
    else
      for rec in $craw; do
        id=${rec%%:*}; rest=${rec#*:}; login=${rest%%:*}; kind=${rest##*:}
        grep -qxF "$id" "$D/seen_c" && continue; echo "$id" >>"$D/seen_c"
        [ -z "$init" ] && continue
        [ "$login" = "$me" ] && continue
        [ "$login" = "$bot" ] && [ "$kind" = "reply" ] && continue
        new="$new; review comment $id by $login ($kind)"
      done
    fi
    rm -f "$cerr"
    rerr=$(mktemp)
    rraw=$(gh api --paginate "repos/$repo/pulls/$pr/reviews?per_page=100" --jq '.[] | "\(.id):\(.user.login):\(.state)"' 2>"$rerr"); rrc=$?
    if [ $rrc -ne 0 ]; then echo "ERROR $repo#$pr $(head -1 "$rerr" | tr -d '\r')"
    else
      for rec in $rraw; do
        id=${rec%%:*}; rest=${rec#*:}; login=${rest%%:*}; state=${rest##*:}
        grep -qxF "$id" "$D/seen_r" && continue; echo "$id" >>"$D/seen_r"
        [ -z "$init" ] && continue
        [ "$login" = "$me" ] && continue
        [ "$login" = "$bot" ] && continue      # bot verdicts are reported via the Assessment line
        new="$new; review $state by $login"
      done
    fi
    rm -f "$rerr"
    icerr=$(mktemp)
    iraw=$(gh api --paginate "repos/$repo/issues/$pr/comments?per_page=100" --jq '.[] | "\(.id):\(.user.login)"' 2>"$icerr"); icrc=$?
    if [ $icrc -ne 0 ]; then echo "ERROR $repo#$pr $(head -1 "$icerr" | tr -d '\r')"
    else
      for rec in $iraw; do
        id=${rec%%:*}; login=${rec##*:}
        grep -qxF "$id" "$D/seen_i" && continue; echo "$id" >>"$D/seen_i"
        [ -z "$init" ] && continue
        [ "$login" = "$me" ] && continue
        new="$new; issue comment $id by $login"
      done
    fi
    rm -f "$icerr"
    [ -n "$new" ] && echo "PR $pr NEW:${new#;}"
    st=$(gh pr view "$pr" --repo "$repo" --json state --jq .state 2>/dev/null)
    [ "$st" = "MERGED" ] && { echo "PR $pr MERGED"; continue; }
    [ "$st" = "CLOSED" ] && { echo "PR $pr CLOSED"; continue; }
    [ -z "$init" ] && putv init 1
    alive="$alive $pr"
  done
  prs=$alive
  [ -z "$prs" ] && exit 0
  sleep 120
done
