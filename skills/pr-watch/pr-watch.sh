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
#   merged/closed (exit). Own replies and bot "thanks" replies are filtered out. A review whose `commit_id`
#   is not the current head is stale, but a human does not automatically re-review after a push, so a
#   human's stale verdict (any state) is still live information this cycle — emitted, marked
#   `(on older head <sha>)`; only a bot's stale verdict is dropped, with a stderr note.
#   Auto-sync (owner decision 2026-09-21): while the PR waits for review, keep its branch merged with the base branch —
#   when it is behind, no human APPROVED review exists and the cooldown has passed, PUT pulls/N/update-branch
#   (GitHub-signed merge commit). Emits BEHIND (approved, 403, 422) / CONFLICTS lines; SYNCED is bookkeeping,
#   not a decision, so it goes to stderr (the Monitor output file) instead of waking the session. PR_WATCH_SYNC=0
#   disables, PR_WATCH_SYNC_COOLDOWN=<s> (default 3600) limits how often a fast-moving base re-triggers CI on the PR.
#   A 422 (merge conflict, or "head ref does not exist" right after the PR merged and its branch was deleted)
#   re-reads the PR state before emitting: merged/closed by then → no BEHIND line, the end-of-cycle check
#   below reports MERGED/CLOSED on its own.
#   The merge commit such a sync lands is tracked silently (SYNCED already said it; no HEAD MOVED follows), and in bot
#   mode the watcher then removes and re-adds the review bot as a requested reviewer itself (RE-REQUEST line only if
#   that fails) — each was a wake-up that asked the session for nothing it could not do here. A head move whose
#   commit this session pushed itself (committer = the configured login, and the sha is already a local git
#   object in PR_WATCH_WORKTREE, when set) is tracked silently the same way — unset PR_WATCH_WORKTREE and every
#   push reads as a real HEAD MOVED (the conservative default; deciding this without a worktree hint is a later step).
#   CHECK NOT GREEN (owner decision 2026-09-22): at most ONE line per head (reset on HEAD MOVED), and only once the suite
#   has settled (no check run queued/in_progress), listing every failing check at that moment — previously one line
#   per newly-finished failing check (~10 wake-ups for one known cause).
#   PR_WATCH_KNOWN_RED=<extended-regex>: before emitting, fetch the failure-level annotations of the failing check
#   runs; if every failing check run has at least one failure annotation and ALL of them match the regex, the line
#   is suppressed (logged to stderr only). A failing check run with zero annotations is unexplained → line emitted.
#   PR_WATCH_REPLAY=1: re-emit the current bot verdict / CHECK NOT GREEN on start even when the state dir already
#   holds this head (default: a re-arm on a known head is silent about what it already reported; the state dir is
#   ${TMPDIR:-/tmp}/pr-watch-<owner>-<repo>-<pr>/ and is shared by every session on the same machine).
# A failed lookup is UNKNOWN, never read as red or green (a swallowed error must never read as a negative
# result, WORKSPACE.md § Verification): every `gh` read a verdict depends on goes through `gh_retry` (3
# attempts, PR_WATCH_RETRY_DELAY seconds apart, default 2 — 0 in tests). A read that still fails after retries
# emits exactly one `PR <n> LOOKUP FAILED: <what> — <error text>` line for that cycle instead of a derived
# `CHECK NOT GREEN` or BEHIND/mergeability alarm from the same missing data, deduped per <what> per PR (the
# same failure persisting across cycles announces once; a change in the error, or a recovery in between, is
# announced again — `lookup_failed`/`lookup_ok` below). PR_WATCH_NOW overrides "now" (tests only; the retry
# delay and the backoff clock both read it).
#   Additive backoff on a settled red: once a `CHECK NOT GREEN` has been announced
#   for a head, the same alarm is re-announced after 1h, then 3h, then 5h … +2h each time, capped by
#   PR_WATCH_BACKOFF_MAX seconds (default 86400) — not on every window, and not never-again either. The first
#   announcement for a head is still immediate. PR_WATCH_KNOWN_RED's mute is unchanged by this: a muted head
#   stays silent (stderr only, once) for as long as it is muted.
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
# gh_retry's caller always reads $GHR_ERR right after `out=$(gh_retry ...)` — but that command substitution
# runs gh_retry in a subshell, so a plain variable assignment inside it never reaches this process. A file
# survives the subshell boundary; ghr_err_file is where gh_retry leaves the trimmed error for the caller to
# read back into $GHR_ERR (helper below).
ghr_err_file="${TMPDIR:-/tmp}/pr-watch-ghrerr.$$"
ghr_err_read() { GHR_ERR=$(cat "$ghr_err_file" 2>/dev/null); }
getv() { cat "$D/$1" 2>/dev/null; }                      # per-PR state: $D is set per round-robin step
putv() { printf '%s\n' "$2" >"$D/$1"; }
now_epoch() { if [ -n "${PR_WATCH_NOW:-}" ]; then printf '%s' "$PR_WATCH_NOW"; else date +%s; fi; }  # PR_WATCH_NOW: tests only
# gh_retry <gh ...>: runs one `gh` read with retry — 3 attempts, PR_WATCH_RETRY_DELAY seconds apart (default
# 2; tests set it near 0 so the suite does not really sleep). Prints the successful stdout; on a final
# failure prints nothing and leaves the last attempt's stderr (first line, trimmed) in $ghr_err_file — call
# ghr_err_read right after to load it into $GHR_ERR (a plain assignment inside gh_retry would not survive the
# `$(gh_retry ...)` subshell the caller needs for stdout). The caller must report that via lookup_failed,
# never read the empty/failed output as "no change" or "all clear".
gh_retry() {
  delay=${PR_WATCH_RETRY_DELAY:-2}; attempt=1
  while :; do
    e=$(mktemp)
    out=$("$@" 2>"$e"); rc=$?
    if [ "$rc" -eq 0 ]; then rm -f "$e"; : >"$ghr_err_file"; printf '%s' "$out"; return 0; fi
    head -1 "$e" | tr -d '\r' >"$ghr_err_file"; rm -f "$e"
    [ "$attempt" -ge 3 ] && return "$rc"
    attempt=$((attempt + 1)); sleep "$delay"
  done
}
# lookup_failed <pr> <what> <err>: the tri-state UNKNOWN event — one line, never a derived CHECK/BEHIND
# alarm from the same failed data (the caller skips that derivation instead of calling this). Deduped per
# <what> per PR in $D/lookup_fail_<what>: the SAME failure persisting across cycles announces once; a
# changed error, or a failure after lookup_ok cleared the marker, announces again.
lookup_failed() {
  pr=$1; what=$2; err=${3:-gh failed with no error text}
  key=$(printf '%s' "$what" | tr -c 'A-Za-z0-9' '_')
  f="$D/lookup_fail_$key"
  prev=$(cat "$f" 2>/dev/null)
  if [ "$prev" != "$err" ]; then
    echo "PR $pr LOOKUP FAILED: $what — $err"
    printf '%s' "$err" >"$f"
  fi
}
lookup_ok() {                                             # clears the marker so a later failure, even with
  what=$2                                                 # identical text, is announced again (it recovered)
  key=$(printf '%s' "$what" | tr -c 'A-Za-z0-9' '_')
  rm -f "$D/lookup_fail_$key"
}
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
    rm -f "$D/seen_bot" "$D/seen_c" "$D/seen_r" "$D/seen_i" "$D/init" "$D/notgreen" "$D/notgreen_bad" "$D/notgreen_last" "$D/notgreen_step" "$D/sync_stuck" "$D/appr_seen" "$D/dirty_seen" "$D/sync_from"
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
    info=$(gh_retry gh api "repos/$repo/pulls/$pr" --jq '"\(.head.sha) \(.base.ref) \(.mergeable_state)"'); irc=$?
    ghr_err_read
    if [ $irc -ne 0 ] || [ -z "$info" ]; then
      # a failed fetch is never "no change" (and never green/red either) — one UNKNOWN line, keep watching
      # next cycle instead of going silent or deriving a CHECK/BEHIND alarm from data that was never read
      lookup_failed "$pr" "PR info" "${GHR_ERR:-empty response}"; alive="$alive $pr"; continue
    fi
    lookup_ok "$pr" "PR info"
    cur=${info%% *}; rest=${info#* }; base=${rest%% *}; mstate=${rest##* }
    case "$cur" in "$head"*) ;; *)
      short=$(printf %s "$cur" | cut -c1-9)
      # This watcher's own update-branch (sync_from = the head it synced FROM) is not news to the session — it
      # already got the SYNCED line. Recognised strictly: the new head is a two-parent GitHub (web-flow) merge commit
      # whose first parent is exactly that head. A second, independent self case: this session's own direct push —
      # recognised only when PR_WATCH_WORKTREE names a local checkout AND the new head's committer is the configured
      # login AND that sha is already a git object there (so a same-login push by someone else's machine, or a login
      # that merely matches by coincidence, still reads as a real move — a surer way to decide this is a later step).
      # Anything else — a push with no worktree hint, someone else's rebase, a lookup failure — is a real HEAD
      # MOVED, as before.
      self=0; resync=0; from=$(getv sync_from)
      if [ -n "$from" ] || [ -n "${PR_WATCH_WORKTREE:-}" ]; then
        cinfo=$(gh api "repos/$repo/commits/$cur" --jq '"\(.parents|length) \(.parents[0].sha // "") \(.committer.login // "")"' 2>/dev/null)
        nparents=${cinfo%% *}; crest=${cinfo#* }; parent1=${crest%% *}; committer=${crest##* }
        if [ -n "$from" ] && [ "$nparents" = 2 ] && [ "$parent1" = "$from" ] && [ "$committer" = web-flow ]; then
          self=1; resync=1
        elif [ -n "${PR_WATCH_WORKTREE:-}" ] && [ -n "$committer" ] && [ "$committer" = "$me" ] &&
             git -C "$PR_WATCH_WORKTREE" cat-file -e "${cur}^{commit}" 2>/dev/null; then
          self=1
        fi
      fi
      rm -f "$D/sync_from"
      if [ "$self" = 1 ]; then
        if [ "$resync" = 1 ]; then echo "pr-watch: PR $pr head $head -> $short is this watcher's own update-branch merge — tracked silently" >&2
        else echo "pr-watch: PR $pr head $head -> $short is this session's own push (committer $me, sha in PR_WATCH_WORKTREE) — tracked silently" >&2
        fi
      else echo "PR $pr HEAD MOVED to $short (was $head)"; fi
      head=$short; putv head "$head"; putv seen_bot 0; rm -f "$D/notgreen" "$D/notgreen_bad" "$D/notgreen_last" "$D/notgreen_step"
      # The review bot does not re-review a merge-commit head on its own, and a plain POST re-request is a no-op there
      # (GitHub thinks it already asked) — remove, then re-add, as pr-merge.sh's force_review does. Only now, once the
      # sync commit is the head: a request sent before it landed would review a head that is about to be replaced.
      # A failure is a line (the session must re-request by hand), never silence. Only the resync case needs this —
      # a real push fires the bot's normal `synchronize` webhook on its own.
      if [ "$resync" = 1 ] && [ -n "$bot" ]; then
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
        behind=$(gh_retry gh api "repos/$repo/compare/$base...$cur" --jq .behind_by); behindrc=$?
        ghr_err_read
        if [ $behindrc -ne 0 ]; then
          # a failed fetch is never "not behind" (that would silently skip a real sync forever) — one
          # UNKNOWN line, never a derived BEHIND/CONFLICTS alarm from data that was never read
          lookup_failed "$pr" "behind-by check" "$GHR_ERR"
        else
          lookup_ok "$pr" "behind-by check"
          if [ "${behind:-0}" -gt 0 ] 2>/dev/null; then
            now=$(now_epoch); last_sync=$(getv last_sync)
            if [ $((now - ${last_sync:-0})) -ge "$sync_cool" ]; then
              araw=$(gh_retry gh api --paginate "repos/$repo/pulls/$pr/reviews?per_page=100"); arc=$?
              ghr_err_read
              if [ $arc -ne 0 ]; then
                # a failed fetch is never "0 approvals" — that would sync (and dismiss) an actually-approved PR;
                # one UNKNOWN line, skip the sync attempt this cycle, retry next cycle instead
                lookup_failed "$pr" "approvals (sync check)" "$GHR_ERR"
              else
                lookup_ok "$pr" "approvals (sync check)"
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
                    # Bookkeeping, not a decision (the session has nothing to do about its own sync) — stderr only.
                    echo "pr-watch: PR $pr SYNCED with $base (was $behind behind): update-branch requested on $(printf %s "$cur" | cut -c1-9) — the merge head is tracked silently (no HEAD MOVED follows) and $rr; further worktree pushes need --rebase" >&2
                    putv last_sync "$now"; putv sync_from "$cur"
                  else
                    case "$out" in
                      *"HTTP 403"*) echo "PR $pr BEHIND $base by $behind — update-branch refused (403: the token lacks the workflow scope, PR touches .github/workflows?) — rebase the worktree + sign-queue --rebase, or on the user's machine: gh pr update-branch $pr";;
                      *"HTTP 422"*)
                        # A 422 here often just means the PR merged (and its branch was deleted) in the gap between
                        # this cycle's fetch and the update-branch call — re-read before emitting: merged/closed by
                        # now means nothing to say here, the end-of-cycle state check below reports MERGED/CLOSED.
                        rst=$(gh pr view "$pr" --repo "$repo" --json state --jq .state 2>/dev/null)
                        case "$rst" in
                          MERGED|CLOSED) : ;;
                          *) echo "PR $pr BEHIND $base by $behind — update-branch 422 (merge conflict or head moved): $(printf %s "$out" | tr '\n' ' ' | cut -c1-200)";;
                        esac
                        ;;
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
      roll=$(gh_retry gh pr view "$pr" --repo "$repo" --json statusCheckRollup --jq '
        .statusCheckRollup[] |
        if .__typename == "StatusContext" then
          (if (.state=="PENDING" or .state=="EXPECTED") then "IN_PROGRESS" else "COMPLETED" end) as $status |
          (if .state=="SUCCESS" then "SUCCESS" elif (.state=="PENDING" or .state=="EXPECTED") then "" else "FAILURE" end) as $concl |
          "\($status)\t\($concl)\t\(.context // "")"
        else
          "\(.status // "")\t\(.conclusion // "")\t\(.name // "")"
        end'); rollrc=$?
      ghr_err_read
      if [ $rollrc -ne 0 ]; then
        # a failed rollup fetch is never "nothing pending, nothing bad" (that reads as a silent GREEN) —
        # one UNKNOWN line, never a derived CHECK NOT GREEN from data that was never read
        lookup_failed "$pr" "check status" "$GHR_ERR"
      else
        lookup_ok "$pr" "check status"
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
          if [ "$mute" = 1 ]; then
            # muted stays muted for as long as it is muted — never populate the backoff state below, so
            # a mute never starts re-announcing itself once PR_WATCH_KNOWN_RED's grace period would've
            # elapsed; that is a different proposal than this one and is deliberately not implemented here
            echo "pr-watch: PR $pr CHECK NOT GREEN muted by PR_WATCH_KNOWN_RED ($bad)" >&2
            putv notgreen "$cur"
          else
            echo "PR $pr CHECK NOT GREEN: $bad"
            putv notgreen "$cur"; putv notgreen_bad "$bad"; putv notgreen_last "$(now_epoch)"; putv notgreen_step 3600
          fi
        fi
      fi
    elif [ -n "$cur" ]; then
      # same head already announced at least once (above) — additive backoff re-announces the same alarm
      # after 1h, then 3h, then 5h … +2h each time, capped by PR_WATCH_BACKOFF_MAX, instead of once-ever
      # (the old behavior) or every 120s window (ten wake-ups for one cause). A muted head never reaches
      # here with bad set (notgreen_bad stays empty), so PR_WATCH_KNOWN_RED's mute is unaffected.
      bad=$(getv notgreen_bad)
      if [ -n "$bad" ]; then
        last=$(getv notgreen_last); step=$(getv notgreen_step); step=${step:-3600}
        now=$(now_epoch)
        if [ $((now - ${last:-0})) -ge "$step" ]; then
          echo "PR $pr CHECK NOT GREEN: $bad"
          putv notgreen_last "$now"
          max=${PR_WATCH_BACKOFF_MAX:-86400}
          newstep=$((step + 7200)); [ "$newstep" -gt "$max" ] && newstep=$max
          putv notgreen_step "$newstep"
        fi
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
    rraw=$(gh api --paginate "repos/$repo/pulls/$pr/reviews?per_page=100" --jq '.[] | "\(.id):\(.user.login):\(.state):\(.commit_id)"' 2>"$rerr"); rrc=$?
    if [ $rrc -ne 0 ]; then echo "ERROR $repo#$pr $(head -1 "$rerr" | tr -d '\r')"
    else
      for rec in $rraw; do
        id=${rec%%:*}; rest=${rec#*:}; login=${rest%%:*}; rest2=${rest#*:}; state=${rest2%%:*}; cid=${rest2#*:}
        grep -qxF "$id" "$D/seen_r" && continue; echo "$id" >>"$D/seen_r"
        [ -z "$init" ] && continue
        [ "$login" = "$me" ] && continue
        [ "$login" = "$bot" ] && continue      # bot verdicts are reported via the Assessment line
        # A verdict on any head but the current one is stale (superseded by what a re-review on the new
        # head would say) — but a human does not automatically re-review after a push, and a CHANGES_REQUESTED
        # keeps blocking the merge regardless of which head it names, so a human's stale verdict is still live
        # information this cycle: emitted in every state, marked with the head it is on. Only a bot's stale
        # verdict is dropped (stderr note only) — a bot re-reviews the new head on its own once asked. "Bot"
        # here is the configured review bot (already skipped just above) or a login in github.bots.
        if [ "$cid" != "$cur" ]; then
          inbots=$(printf '%s' "$bots" | jq -e --arg l "$login" 'type == "array" and (index($l) != null)' 2>/dev/null)
          if [ "$inbots" = "true" ]; then
            echo "pr-watch: PR $pr dropping stale review $id ($state by $login) on $(printf %s "$cid" | cut -c1-9), current head is $(printf %s "$cur" | cut -c1-9)" >&2
          else
            new="$new; review $state by $login (on older head $(printf %s "$cid" | cut -c1-9))"
          fi
          continue
        fi
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
