#!/bin/sh
# enqueue.sh — put a commit+push job on the workspace owner's sign queue (run by any Claude session).
#
#   enqueue.sh <topic> <abs-worktree> <branch> <abs-msg-file> [--rebase] [--new-branch] [--files "<paths>"]
#
#   --rebase       fetch origin <branch> and rebase -S onto FETCH_HEAD before pushing (remote is ahead,
#                  e.g. after a GitHub "Update branch" click). Default: plain push.
#   --new-branch   first push of the branch (push -u).
#   --files        stage only these paths (space separated, relative to the worktree) instead of -A.
#   --by <name>    your session name (as shown by ListAgents) so a failure can be routed back to you.
#   --onto <upstream-branch>:<old-base-sha>
#                  stacked branch: after the commit, fetch origin <upstream-branch> and
#                  `rebase -S --onto FETCH_HEAD <old-base-sha>`, so every commit after <old-base-sha>
#                  (the local placeholder that stood in for the upstream branch's unsigned tree) is
#                  replayed on the upstream's real remote tip and the placeholder itself is dropped.
#                  Enqueue the upstream branch's job first: the fetch fails if it was never pushed.
#   --force-with-lease <remote-sha>
#                  the branch was ALREADY pushed and its history is being rewritten (a second --onto
#                  re-stack, an interactive fixup): push with --force-with-lease=refs/heads/<branch>:<remote-sha>
#                  so the push only lands if the remote still points at the tip you inspected (a plain push
#                  after --onto is rejected as non-fast-forward).
#                  Exclusive with --new-branch. Verify <remote-sha> via `gh api repos/<o>/<r>/branches/<branch>`.
#   --ticket <KEY> --epic <KEY> --pr <n> --summary "<text>"
#                  overview metadata for the owner's `make sign` table (signq.py). All optional: ticket is derived
#                  from the branch/topic, epic from the .context/ epic doc that mentions the ticket, PR via
#                  `gh pr list --head`, summary = first line of the message file. Pass them when you know
#                  better (a new branch has no PR yet; a ticket outside any context doc has no epic).
#
# The job is a self-contained POSIX sh script under .context/state/sign-queue/ that sign.sh runs on the
# host. Before enqueuing, verify `git -C <wt> status --short` shows exactly what the commit should
# contain and that the message file exists. One job = one commit.
set -eu
eval "$(python3 "$(dirname "$0")/../../context-db/bin/kit_profile.py" identity-env 2>/dev/null)"  # WORKSPACE_* from plugin userConfig, if set
# the queue lives in the workspace (#7): `<.context>/state/sign-queue/`, never under the kit (a plugin update deletes it)
if [ -z "${SIGN_QUEUE_DIR:-}" ]; then
  ctx=$(python3 "$(dirname "$0")/../../context-db/bin/kit_profile.py" context)
  [ -d "$ctx" ] || { echo "enqueue.sh: no workspace .context/ found ($ctx) — run from the workspace, or set SIGN_QUEUE_DIR" >&2; exit 2; }
  SIGN_QUEUE_DIR="$ctx/state/sign-queue"
fi
Q=$SIGN_QUEUE_DIR
topic=$1; wt=$2; br=$3; msg=$4; shift 4
rebase=0; newbr=0; files=""; explicit_files=""; by="${SIGN_QUEUE_BY:-${WORKSPACE_USER:-?}}"; onto=""; lease=""
ticket=""; epic=""; pr=""; summary=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rebase) rebase=1;; --new-branch) newbr=1;; --files) files=$2; explicit_files=1; shift;; --by) by=$2; shift;;
    --onto) onto=$2; shift;;
    --force-with-lease) lease=$2; shift;;
    --ticket) ticket=$2; shift;; --epic) epic=$2; shift;; --pr) pr=$2; shift;; --summary) summary=$2; shift;;
    *) echo "unknown flag $1" >&2; exit 2;;
  esac; shift
done
[ -d "$wt/.git" ] || [ -f "$wt/.git" ] || { echo "no worktree at $wt" >&2; exit 2; }
[ -f "$msg" ] || { echo "no message file at $msg" >&2; exit 2; }
# commit style (WORKSPACE.md § Rules): Conventional Commits unless the repo overrides it — resolved by
# commit_style.py from the worktree's repo marker / commitlint / env config. SIGN_QUEUE_SKIP_STYLE=1 = deliberate one-off.
if [ "${SIGN_QUEUE_SKIP_STYLE:-0}" != "1" ]; then
  python3 "$(cd "$(dirname "$0")/../.." && pwd)/context-db/bin/commit_style.py" check --quiet --dir "$wt" "$msg" \
    || { echo "message file $msg does not follow the repo's commit style — fix the subject (or SIGN_QUEUE_SKIP_STYLE=1 for a deliberate one-off)" >&2; exit 2; }
fi
if [ -z "$(git -C "$wt" status --short)" ]; then
  # clean worktree: allow a push-only retry (commit already made on a previous drain, push failed)
  # origin/<branch> may not exist (single-branch clone) — compare against the remote tip via ls-remote.
  remote_tip=$(git -C "$wt" ls-remote --heads origin "$br" 2>/dev/null | cut -f1)
  if [ -n "$remote_tip" ] && git -C "$wt" cat-file -e "$remote_tip^{commit}" 2>/dev/null; then
    ahead=$(git -C "$wt" rev-list --count "$remote_tip..HEAD" 2>/dev/null || echo 0)
  else
    ahead=$(git -C "$wt" rev-list --count "origin/$br..HEAD" 2>/dev/null || echo 0)
  fi
  [ "${ahead:-0}" -gt 0 ] || { echo "nothing to commit in $wt (and HEAD is not ahead of origin/$br)" >&2; exit 2; }
  echo "note: worktree clean, HEAD is $ahead commit(s) ahead of origin/$br -> push-only retry job" >&2
fi
case "$msg" in "$wt"/*)
  # the default `add -A` would commit the message file itself if it lives inside the worktree
  if [ -z "$files" ]; then echo "message file $msg is inside the worktree and no --files given: add -A would commit it. Put it under <workspace root>/.worktrees/ or pass --files" >&2; exit 2; fi;;
esac
case "$topic" in *[!A-Za-z0-9._-]*) echo "topic must be [A-Za-z0-9._-]" >&2; exit 2;; esac
if [ -n "$onto" ]; then
  case "$onto" in *:*) ;; *) echo "--onto expects <upstream-branch>:<old-base-sha>" >&2; exit 2;; esac
  onto_br=${onto%%:*}; onto_base=${onto#*:}
  git -C "$wt" cat-file -e "$onto_base^{commit}" 2>/dev/null || { echo "--onto: $onto_base is not a commit in $wt" >&2; exit 2; }
  [ $rebase = 0 ] || { echo "--onto and --rebase are exclusive (the --onto rebase already moves the branch)" >&2; exit 2; }
fi
if [ -n "$lease" ]; then
  case "$lease" in *[!0-9a-f]*|"") echo "--force-with-lease expects the remote tip sha" >&2; exit 2;; esac
  [ $newbr = 0 ] || { echo "--force-with-lease and --new-branch are exclusive" >&2; exit 2; }
fi
if [ -n "$files" ]; then
  # `git add -- <path>` fails ("pathspec did not match") on a deletion that is ALREADY staged (git rm):
  # such paths are in neither worktree nor index. They are already part of the commit, so drop them
  # from the add line; anything else that is neither present nor tracked is a typo -> refuse.
  kept=""
  for f in $files; do
    # shellcheck disable=SC2089  # the quoted paths are written into a job script, not evaluated here
    if [ -e "$wt/$f" ] || git -C "$wt" ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then kept="$kept '$f'"   # single-quoted: some web frameworks' route dirs contain \$param (unquoted, set -u aborts the job)
    elif git -C "$wt" diff --cached --name-only --diff-filter=D | grep -qx "$f"; then echo "note: $f is an already-staged deletion, included via the index" >&2
    else echo "--files: $f is neither in the worktree nor tracked" >&2; exit 2; fi
  done
  files=$kept
fi
# overview metadata (ticket / epic / repo / PR / subject) for `make sign`; best effort — a job without it
# still runs, signq.py then derives what it can from the header + worktree.
flags=""
[ $rebase = 1 ] && flags="$flags,rebase"; [ $newbr = 1 ] && flags="$flags,new-branch"
[ -n "$onto" ] && flags="$flags,onto"; [ -n "$lease" ] && flags="$flags,force-with-lease"; [ -n "$files" ] && flags="$flags,files"
nfiles=-1
# shellcheck disable=SC2086,SC2090  # $files is a whitespace list of quoted paths; split on purpose to count entries
[ -n "$files" ] && nfiles=$(printf '%s\n' $files | grep -c .)
meta=$(python3 "$(dirname "$0")/signq.py" meta "$wt" "$br" "$msg" --topic "$topic" --by "$by" --ticket "$ticket" \
         --epic "$epic" --pr "$pr" --summary "$summary" --flags "${flags#,}" --files "$nfiles" 2>/dev/null || true)
mkdir -p "$Q"  # the workspace queue dir is created on first use (#7) — nothing ships or seeds it
job="$Q/$(date -u +%Y%m%dT%H%M%SZ)-$topic.sh"
tmp="$job.tmp"
{
  echo '#!/bin/sh'
  echo "# sign-queue job: $topic  (enqueued $(date -u +%FT%TZ) by session $by)"
  echo "# worktree $wt  branch $br"
  [ -n "$meta" ] && echo "# META $meta"
  echo 'set -eu'
  echo "WT='$wt'; BR='$br'; MSG='$msg'"
  if [ -n "$files" ]; then echo "git -C \"\$WT\" add -- $files"; elif [ -n "$explicit_files" ]; then echo '# all listed files were already staged'; else echo 'git -C "$WT" add -A'; fi
  echo 'git -C "$WT" diff --cached --stat'
  echo '# commit only if something is staged (a retry after a failed push must not fail here)'
  echo 'if ! git -C "$WT" diff --cached --quiet; then git -C "$WT" commit -S -F "$MSG"; else echo "(already committed)"; fi'
  if [ $rebase = 1 ]; then
    echo 'git -C "$WT" fetch origin "$BR"'
    # FETCH_HEAD, not origin/$BR: a single-branch / partial clone (refspec = main only)
    # never creates refs/remotes/origin/<branch>, so origin/$BR is an "invalid upstream".
    echo 'git -C "$WT" rebase -S FETCH_HEAD'
  fi
  if [ -n "$onto" ]; then
    echo "UP='$onto_br'; OLD_BASE='$onto_base'"
    echo '# stacked branch: replay our commits on the upstream branch'"'"'s pushed tip, dropping the local placeholder'
    echo 'git -C "$WT" fetch origin "$UP"'
    echo 'git -C "$WT" rebase -S --onto FETCH_HEAD "$OLD_BASE"'
  fi
  if [ $newbr = 1 ]; then echo 'git -C "$WT" push -u origin "$BR"'
  elif [ -n "$lease" ]; then echo "LEASE='$lease'"; echo '# rewritten history of an already-pushed branch: land only if the remote tip is still the one we inspected'; echo 'git -C "$WT" push --force-with-lease="refs/heads/$BR:$LEASE" origin "$BR"'
  else echo 'git -C "$WT" push origin "$BR"'; fi
  echo 'git -C "$WT" log --format="pushed %h %G? %s" -1'
} > "$tmp"
mv "$tmp" "$job"
[ -n "$meta" ] && printf '%s' "$meta" | python3 -c '
import json,sys; m=json.load(sys.stdin)
pr=("#%s" % m["pr"]) if m.get("pr") else "no PR yet"
print("queued %s: %s · epic %s · %s %s · %s" % (m.get("topic"), m.get("ticket") or "?", m.get("epic") or "?",
      (m.get("repo") or "?").split("/")[-1], pr, m.get("subject") or ""))' >&2
echo "$job"
