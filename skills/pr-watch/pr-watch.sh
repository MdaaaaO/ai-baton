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
#   CHECK NOT GREEN (owner decision 2026-09-22): first announced at most ONCE per head (reset on HEAD MOVED), and only
#   once the suite has settled (no check run queued/in_progress), listing every failing check at that moment —
#   previously one line per newly-finished failing check (~10 wake-ups for one known cause). A head that stays red
#   repeats the line on the backoff described below.
#   PR_WATCH_KNOWN_RED=<extended-regex>: before emitting, fetch the failure-level annotations of the failing check
#   runs; if every failing check run has at least one failure annotation and ALL of them match the regex, the line
#   is suppressed (logged to stderr only). A failing check run with zero annotations is unexplained → line emitted.
#   A mute that has suppressed a line on a PR (recorded with the regex it happened under) expires for that PR
#   once its checks are next green and settled: one stderr note, and a later red — even one whose annotations
#   match the same regex — is reported like any other. The record (and the expiry) belongs to the PR, not to a
#   head: it survives a re-arm, on the same head or a new one. A changed regex mutes again from scratch, and
#   PR_WATCH_REPLAY=1 starts over. An expired mute never re-runs the annotation fetch.
#   PR_WATCH_REPLAY=1: re-emit the current bot verdict / CHECK NOT GREEN on start even when the state dir already
#   holds this head (default: a re-arm on a known head is silent about what it already reported; the state dir is
#   ${TMPDIR:-/tmp}/pr-watch-<owner>-<repo>-<pr>/ and is shared by every session on the same machine).
# A failed lookup is UNKNOWN, never red and never green (WORKSPACE.md § Verification: a swallowed error must
# never read as a negative result). Every read an event depends on (PR info, behind-by, the reviews, the check
# rollup, the two comment listings) goes through `gh_retry`: 3 attempts, PR_WATCH_RETRY_DELAY seconds apart
# (default 2). The reviews are read once per cycle and feed the sync's approval count, the bot verdict and the
# review listing. A read that still fails prints one `PR <n> LOOKUP FAILED: <what> — <error text>` line and
# every step that needed it is skipped for the cycle: no CHECK NOT GREEN, no BEHIND line, no bot verdict and
# no sync is derived from data that was never read.
#   An alarm that persists backs off additively: after its first line it is silent for 1h, then 3h, then 5h …
#   (+2h each time), capped by PR_WATCH_BACKOFF_MAX seconds (default 86400). Two alarms use it: a CHECK NOT
#   GREEN on an unchanged head (the rollup is read again first: checks that went green end the alarm, and the
#   line lists what is red now) and a LOOKUP FAILED with the same error text (` (still failing)` appended).
#   A lookup that works again, or fails with another error, starts over. PR_WATCH_KNOWN_RED is unchanged: a
#   muted head stays silent. PR_WATCH_NOW overrides "now" for the backoff and the sync cooldown (tests only).
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
now_epoch() { if [ -n "${PR_WATCH_NOW:-}" ]; then printf '%s' "$PR_WATCH_NOW"; else date +%s; fi; }  # PR_WATCH_NOW: tests only
# gh_retry <gh ...>: one `gh` read, up to 3 attempts. The output is left in $ghr_out; after a failed last
# attempt $ghr_err holds the first line of its stderr. Call it as a plain command, never inside $( ): a
# subshell would lose both variables.
gh_retry() {
  ghr_n=1; ghr_e=$(mktemp)
  while :; do
    ghr_out=$("$@" 2>"$ghr_e"); ghr_rc=$?
    if [ "$ghr_rc" -eq 0 ]; then ghr_err=""; rm -f "$ghr_e"; return 0; fi
    ghr_err=$(head -1 "$ghr_e" | tr -d '\r')
    [ "$ghr_n" -ge 3 ] && { rm -f "$ghr_e"; return "$ghr_rc"; }
    ghr_n=$((ghr_n + 1)); sleep "${PR_WATCH_RETRY_DELAY:-2}"
  done
}
# Additive backoff of one persisting alarm, per PR: $D/<name>_last is when its last line went out,
# $D/<name>_step how long it stays silent after that (3600, then +7200 per line; the cap bounds every window).
backoff_cap() { bo_max=${PR_WATCH_BACKOFF_MAX:-86400}; if [ "$1" -gt "$bo_max" ]; then printf '%s' "$bo_max"; else printf '%s' "$1"; fi; }
backoff_start() { putv "$1_last" "$(now_epoch)"; putv "$1_step" "$(backoff_cap 3600)"; }
backoff_due() {
  bo_last=$(getv "$1_last"); bo_step=$(getv "$1_step")
  [ -n "$bo_last" ] && [ $(( $(now_epoch) - bo_last )) -ge "${bo_step:-3600}" ]
}
backoff_widen() {
  bo_step=$(getv "$1_step")
  putv "$1_last" "$(now_epoch)"; putv "$1_step" "$(backoff_cap $(( ${bo_step:-3600} + 7200 )))"
}
# lookup_failed <pr> <what> <err>: the UNKNOWN event. One line when <what> starts failing or its error text
# changes; the same error on later cycles is silent until the backoff window has passed. The caller skips
# whatever the read was for. lookup_ok <what> forgets the failure, so the next one is announced again.
lookup_key() { printf 'lookup_fail_%s' "$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '_')"; }
lookup_failed() {
  lf_key=$(lookup_key "$2"); lf_err=${3:-gh gave no error text}
  if [ "$(getv "$lf_key")" != "$lf_err" ]; then
    echo "PR $1 LOOKUP FAILED: $2 — $lf_err"; putv "$lf_key" "$lf_err"; backoff_start "$lf_key"
  elif backoff_due "$lf_key"; then
    echo "PR $1 LOOKUP FAILED: $2 — $lf_err (still failing)"; backoff_widen "$lf_key"
  fi
}
lookup_ok() { lf_key=$(lookup_key "$1"); rm -f "$D/$lf_key" "$D/${lf_key}_last" "$D/${lf_key}_step"; }
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
    # the known-red record is per PR, not per head: a new head keeps it, only a replay starts it over
    if [ "${PR_WATCH_REPLAY:-0}" = 1 ]; then rm -f "$D/known_red_used" "$D/known_red_expired"; fi
    rm -f "$D"/lookup_fail_*                             # a lookup failure announced before the reset is said again
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
    gh_retry gh api "repos/$repo/pulls/$pr" --jq '"\(.head.sha) \(.base.ref) \(.mergeable_state)"'; irc=$?; info=$ghr_out
    if [ $irc -ne 0 ] || [ -z "$info" ]; then
      # a failed fetch is never "no change": say so and keep watching next cycle instead of going silent
      lookup_failed "$pr" "PR info" "${ghr_err:-empty response}"; alive="$alive $pr"; continue
    fi
    lookup_ok "PR info"
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
    # One reviews read per cycle feeds three steps: the sync's approval count, the bot verdict and the review
    # listing. A read that failed is announced once, here; each of the three then skips itself this cycle.
    gh_retry gh api --paginate "repos/$repo/pulls/$pr/reviews?per_page=100"; revrc=$?; revs=""
    if [ $revrc -eq 0 ]; then
      revs=$(printf '%s' "$ghr_out" | jq -sce 'add // [] | select(type == "array")' 2>/dev/null) || { revrc=1; ghr_err="the reviews listing is not a JSON list"; }
    fi
    if [ $revrc -ne 0 ]; then lookup_failed "$pr" "reviews" "$ghr_err"; else lookup_ok "reviews"; fi
    # keep the branch merged with its base while we wait for reviews (we cannot merge anyway, so a stale branch only delays the merge)
    if [ "$sync" = 1 ] && [ -n "$cur" ] && [ -n "$base" ] && [ "$(getv sync_stuck)" != "$cur" ]; then
      if [ "$mstate" = dirty ]; then
        if [ "$(getv dirty_seen)" != "$cur" ]; then echo "PR $pr CONFLICTS with $base — rebase the worktree and enqueue sign-queue --rebase; auto-sync skipped"; putv dirty_seen "$cur"; fi
      else
        gh_retry gh api "repos/$repo/compare/$base...$cur" --jq .behind_by; behindrc=$?; behind=$ghr_out
        if [ $behindrc -ne 0 ]; then
          # a failed fetch is never "not behind": that would skip a sync the branch needs
          lookup_failed "$pr" "behind-by check" "$ghr_err"
        else
          lookup_ok "behind-by check"
          if [ "${behind:-0}" -gt 0 ] 2>/dev/null; then
            now=$(now_epoch); last_sync=$(getv last_sync)
            if [ $((now - ${last_sync:-0})) -ge "$sync_cool" ]; then
              if [ $revrc -ne 0 ]; then
                # a failed reviews read (announced above) is never "0 approvals" — that would sync (and dismiss)
                # an actually-approved PR; skip the sync attempt this cycle, retry next cycle instead
                :
              else
                # Neither the configured review bot's own approval nor a login in the configured `github.bots`
                # list (which auto-merge.yml's REVIEWER — the identity its own approval carries, a different
                # login than github.review_bot when the bot posts its Assessment under its own account —
                # defaults into; never hardcode that login here, gh-cli SKILL.md) counts as the human approval
                # that would make a push dismiss something worth keeping: without this exclusion a PR approved
                # only by one of those never gets synced while it sits BEHIND, and auto-merge — which requires a
                # clean, non-BEHIND head — never runs.
                appr=$(printf '%s' "$revs" | jq --arg bot "$bot" --argjson bots "$bots" '[.[] | select(.state=="APPROVED" and .user.login!=$bot and ((.user.login as $l | ($bots | index($l))) == null))] | length' 2>/dev/null) || appr=""  # gh api --jq has no --arg (gh-cli skill)
                if [ -z "$appr" ]; then
                  # a count that could not be computed is not "0 approvals" either
                  lookup_failed "$pr" "approval count" "the approvals in the reviews listing could not be counted"
                elif [ "$appr" -gt 0 ] 2>/dev/null; then
                  lookup_ok "approval count"
                  if [ "$(getv appr_seen)" != "$cur" ]; then echo "PR $pr BEHIND $base by $behind but APPROVED — not auto-syncing (a push would dismiss the approval where dismiss_stale_reviews is on): merge now, or update-branch and ask for re-approval"; putv appr_seen "$cur"; fi
                else
                  lookup_ok "approval count"
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
    # The verdict is taken from this cycle's reviews (no second read); without them it stays unknown.
    if [ -n "$bot" ] && [ "$(getv seen_bot)" = 0 ] && [ $revrc -eq 0 ]; then
      printf '%s' "$revs" >"$D/.reviews.json"
      v=$(PR_WATCH_BOT_LOGIN=$bot bash "$KIT/skills/pr-watch/bot-verdict.sh" "$repo" "$pr" "$head" "$D/.reviews.json" 2>"$D/.verr"); vrc=$?
      case $vrc in
        0) lookup_ok "bot verdict"; [ "$v" = none ] || { putv seen_bot 1; echo "PR $pr BOT REVIEW on $head: $v"; } ;;
        2) lookup_ok "bot verdict" ;;                      # bot became unconfigured mid-run — nothing to report
        *) verr=$(head -1 "$D/.verr" | tr -d '\r'); lookup_failed "$pr" "bot verdict" "${verr:-bot-verdict.sh exited $vrc}" ;;
      esac
      rm -f "$D/.verr" "$D/.reviews.json"
    fi
    # Checks: first one line per head, only once nothing is still running, listing every failing check.
    # statusCheckRollup mixes two node shapes: a CheckRun (status/conclusion/name) and a legacy commit status,
    # a StatusContext (state/context only — no status or conclusion field at all). Reading status/conclusion off
    # every node without telling them apart drops every StatusContext silently: it never counts as pending and
    # never counts as bad, so a PR whose only red signal is an external CI status (a required status context)
    # gets reported green. Map state to the same tri-state conclusion/status pair a CheckRun carries instead.
    # A head that already has its line is read again only when the backoff window of that line has passed.
    # A PR_WATCH_KNOWN_RED mute that has suppressed a line on this PR (known_red_used holds the regex it
    # was suppressed under) stays live until a green settles the suite — so the rollup is re-read every
    # cycle while live, even on an unchanged head, to notice that green and expire the mute (one read; a
    # head that is still red and already muted costs no annotation lookup). Once expired
    # (known_red_expired holds the same regex) it reverts to the plain once-per-head/backoff rule above —
    # no more forced re-reads, and the mute decision below is skipped outright (no annotation lookups).
    ng=$(getv notgreen); ng_bad=$(getv notgreen_bad)
    kru=$(getv known_red_used); kre=$(getv known_red_expired); muted_live=0
    if [ -n "$known_red" ] && [ "$kru" = "$known_red" ] && [ "$kre" != "$known_red" ]; then muted_live=1; fi
    if [ -n "$cur" ] && { [ "$ng" != "$cur" ] || { [ -n "$ng_bad" ] && backoff_due notgreen; } || [ "$muted_live" = 1 ]; }; then
      gh_retry gh pr view "$pr" --repo "$repo" --json statusCheckRollup --jq '
        .statusCheckRollup[] |
        if .__typename == "StatusContext" then
          (if (.state=="PENDING" or .state=="EXPECTED") then "IN_PROGRESS" else "COMPLETED" end) as $status |
          (if .state=="SUCCESS" then "SUCCESS" elif (.state=="PENDING" or .state=="EXPECTED") then "" else "FAILURE" end) as $concl |
          "\($status)\t\($concl)\t\(.context // "")"
        else
          "\(.status // "")\t\(.conclusion // "")\t\(.name // "")"
        end'; rollrc=$?; roll=$ghr_out
      if [ $rollrc -ne 0 ]; then
        # a failed rollup fetch is never "nothing pending, nothing bad": that would read as green
        lookup_failed "$pr" "check status" "$ghr_err"
      else
        lookup_ok "check status"
        pend=$(printf '%s\n' "$roll" | awk -F'\t' '$1=="QUEUED"||$1=="IN_PROGRESS"||$1=="PENDING"||$1=="WAITING"||$1=="REQUESTED"' | grep -c . )
        bad=$(printf '%s\n' "$roll" | awk -F'\t' '$1=="COMPLETED" && $2!="" && $2!="SUCCESS" && $2!="SKIPPED" && $2!="NEUTRAL" {print $3": "$2}' | sort -u | tr '\n' ';')
        if [ -n "$bad" ] && [ "$pend" = 0 ] && [ "$muted_live" = 1 ] && [ "$ng" = "$cur" ] && [ -z "$ng_bad" ]; then
          :  # this head is already muted and still red: no second annotation lookup, no second note
        elif [ -n "$bad" ] && [ "$pend" = 0 ]; then
          mute=0
          if [ -n "$known_red" ] && [ "$kre" != "$known_red" ]; then
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
            # a muted head keeps no backoff state: it stays silent for as long as it is muted. Record which
            # regex did the muting (known_red_used) — a later green on this PR, read with the same regex
            # still configured, expires the mute (below); a changed regex here just overwrites the record.
            echo "pr-watch: PR $pr CHECK NOT GREEN muted by PR_WATCH_KNOWN_RED ($bad)" >&2
            rm -f "$D/notgreen_bad" "$D/notgreen_last" "$D/notgreen_step"
            putv known_red_used "$known_red"
          else
            echo "PR $pr CHECK NOT GREEN: $bad"
            if [ "$ng" = "$cur" ]; then backoff_widen notgreen; else backoff_start notgreen; fi
            putv notgreen_bad "$bad"
          fi
          putv notgreen "$cur"
        elif [ "$pend" = 0 ]; then
          if [ "$ng" = "$cur" ]; then
            # the red this head was announced with is gone (a re-run went green): nothing left to repeat. The
            # head is forgotten, so a check that goes red on it later is announced like a first red.
            echo "pr-watch: PR $pr checks on $(printf %s "$cur" | cut -c1-9) are no longer red — CHECK NOT GREEN is not repeated" >&2
            rm -f "$D/notgreen" "$D/notgreen_bad" "$D/notgreen_last" "$D/notgreen_step"
          fi
          if [ "$muted_live" = 1 ] && [ -n "$roll" ]; then
            # a mute that actually suppressed something on this PR expires once its checks settle green —
            # from here a red that matches the same regex is reported like any other (no annotation lookup).
            # A rollup with no entry is not a green: right after a push no check has registered yet.
            echo "pr-watch: PR $pr PR_WATCH_KNOWN_RED mute expired — checks are green on $(printf %s "$cur" | cut -c1-9)" >&2
            putv known_red_expired "$known_red"; rm -f "$D/known_red_used"
          fi
        fi
      fi
    fi
    new=""
    # review comments: skip own, skip bot in-thread replies. A failed page (rate limit, a transient 5xx) must
    # never read the same as "no new comments" — that silently drops whatever page failed, possibly for good
    # (the ids on it are never retried once the state dir has moved past them); report it and skip this listing
    # for the cycle instead.
    gh_retry gh api --paginate "repos/$repo/pulls/$pr/comments?per_page=100" --jq '.[] | "\(.id):\(.user.login):\(if .in_reply_to_id then "reply" else "top" end)"'; crc=$?; craw=$ghr_out
    if [ $crc -ne 0 ]; then lookup_failed "$pr" "review comments" "$ghr_err"
    else
      lookup_ok "review comments"
      for rec in $craw; do
        id=${rec%%:*}; rest=${rec#*:}; login=${rest%%:*}; kind=${rest##*:}
        grep -qxF "$id" "$D/seen_c" && continue; echo "$id" >>"$D/seen_c"
        [ -z "$init" ] && continue
        [ "$login" = "$me" ] && continue
        [ "$login" = "$bot" ] && [ "$kind" = "reply" ] && continue
        new="$new; review comment $id by $login ($kind)"
      done
    fi
    if [ $revrc -eq 0 ]; then
      rraw=$(printf '%s' "$revs" | jq -r '.[] | "\(.id):\(.user.login):\(.state):\(.commit_id)"')
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
    gh_retry gh api --paginate "repos/$repo/issues/$pr/comments?per_page=100" --jq '.[] | "\(.id):\(.user.login)"'; icrc=$?; iraw=$ghr_out
    if [ $icrc -ne 0 ]; then lookup_failed "$pr" "issue comments" "$ghr_err"
    else
      lookup_ok "issue comments"
      for rec in $iraw; do
        id=${rec%%:*}; login=${rec##*:}
        grep -qxF "$id" "$D/seen_i" && continue; echo "$id" >>"$D/seen_i"
        [ -z "$init" ] && continue
        [ "$login" = "$me" ] && continue
        new="$new; issue comment $id by $login"
      done
    fi
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
