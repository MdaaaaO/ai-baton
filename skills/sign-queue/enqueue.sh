#!/bin/sh
# enqueue.sh — put a commit+push job on the workspace owner's sign queue (run by any Claude session).
#
#   enqueue.sh <topic> <abs-worktree> <branch> <abs-msg-file> [--rebase] [--new-branch] [--files "<paths>" | --all]
#
#   --rebase       fetch origin <branch> and rebase -S onto FETCH_HEAD before pushing (remote is ahead,
#                  e.g. after a GitHub "Update branch" click). Default: plain push.
#   --new-branch   first push of the branch (push -u).
#   --files        stage only these paths (space separated, relative to the worktree) — the norm; explicit
#                  paths are always what a commit should carry.
#   --all          stage with `git add -A` on purpose. Exclusive with --files. Neither flag given falls back
#                  to `git add -A` too, but prints a warning either way — `-A` should never be a silent default.
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
#   --supersede    fold this fix into the newest PENDING job of the same topic (and, when --by is also given,
#                  the same --by) instead of adding a second job for it: that job is deleted and its name
#                  printed, then this enqueue proceeds as usual. Refuses (exit 2, nothing touched) when the
#                  candidate already has a drain log or a parked `.failed` — it was attempted, not just queued,
#                  and dropping it would lose that history. A round the owner has not drained yet stays one job.
#
# The job is a self-contained POSIX sh script under .context/state/sign-queue/ that sign.sh runs on the
# host. Before enqueuing, verify `git -C <wt> status --short` shows exactly what the commit should
# contain and that the message file exists. One job = one commit.
set -eu
# WORKSPACE_* from plugin userConfig, if set — a failed lookup stops here instead of queuing a job without an identity (#123)
idenv=$(python3 "$(dirname "$0")/../../context-db/bin/kit_profile.py" identity-env) \
  || { echo "enqueue.sh: kit_profile.py identity-env failed — fix it before queuing" >&2; exit 2; }
eval "$idenv"
# The job is shell code run on the host with the signing key: every value goes in through sq(), never raw (#123).
# sq VALUE — VALUE as one single-quoted sh word ('…' with each ' written as '\''). Values are single-line (no_nl).
sq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
NL='
'
no_nl() { case "$2" in *"$NL"*) echo "enqueue.sh: $1 contains a newline — refused" >&2; exit 2;; esac; }
# the queue lives in the workspace (#7): `<.context>/state/sign-queue/`, never under the kit (a plugin update deletes it)
if [ -z "${SIGN_QUEUE_DIR:-}" ]; then
  ctx=$(python3 "$(dirname "$0")/../../context-db/bin/kit_profile.py" context)
  [ -d "$ctx" ] || { echo "enqueue.sh: no workspace .context/ found ($ctx) — run from the workspace, or set SIGN_QUEUE_DIR" >&2; exit 2; }
  SIGN_QUEUE_DIR="$ctx/state/sign-queue"
  SIGN_QUEUE_CONTEXT="$ctx"
fi
# exported once resolved: the `signq.py meta` call below, and any signq.py this shell goes on to run, must land
# on the exact same queue directory this enqueue just resolved — never let a later call re-derive its own.
export SIGN_QUEUE_DIR
[ -n "${SIGN_QUEUE_CONTEXT:-}" ] && export SIGN_QUEUE_CONTEXT
Q=$SIGN_QUEUE_DIR
topic=$1; wt=$2; br=$3; msg=$4; shift 4
rebase=0; newbr=0; files=""; explicit_files=""; all=0; by="${SIGN_QUEUE_BY:-${WORKSPACE_USER:-?}}"; onto=""; lease=""
ticket=""; epic=""; pr=""; summary=""; supersede=0; by_given=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rebase) rebase=1;; --new-branch) newbr=1;; --files) files=$2; explicit_files=1; shift;; --all) all=1;;
    --by) by=$2; by_given=1; shift;;
    --onto) onto=$2; shift;;
    --force-with-lease) lease=$2; shift;;
    --ticket) ticket=$2; shift;; --epic) epic=$2; shift;; --pr) pr=$2; shift;; --summary) summary=$2; shift;;
    --supersede) supersede=1;;
    *) echo "unknown flag $1" >&2; exit 2;;
  esac; shift
done
if [ -n "$files" ] && [ $all = 1 ]; then echo "--files and --all are exclusive" >&2; exit 2; fi
no_nl worktree "$wt"; no_nl branch "$br"; no_nl "message path" "$msg"; no_nl --files "$files"; no_nl --onto "$onto"; no_nl --by "$by"
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
if [ $supersede = 1 ]; then
  # every job's line 2 is `# sign-queue job: <topic>  (enqueued <date> by session <by>)` (fixed shape,
  # written below) — the newest PENDING job (job names sort chronologically: a UTC timestamp prefix)
  # whose topic (and, when --by was given, whose by) matches is the fold target.
  cand=""
  for f in "$Q"/*.sh; do
    [ -e "$f" ] || continue
    hdr=$(sed -n '2p' "$f" 2>/dev/null || true)
    case "$hdr" in "# sign-queue job: $topic  "*) ;; *) continue;; esac
    if [ -n "$by_given" ]; then
      case "$hdr" in *" by session $by)") ;; *) continue;; esac
    fi
    cand=$f  # "$Q"/*.sh globs in sorted order: the last match is the newest
  done
  if [ -n "$cand" ]; then
    cbase=$(basename "$cand")
    if [ -f "$Q/logs/$cbase.log" ] || [ -f "$Q/$cbase.failed" ]; then
      echo "enqueue.sh: --supersede refuses $cbase — it already has a drain log or a parked failure; resolve it by hand (make sign_show / sign_log / sign_retry) first" >&2
      exit 2
    fi
    # deletion itself is deferred to just before the new job is installed (below the --onto/--force-with-lease/
    # --files/signq.py meta checks): any of those can still `exit 2` and must leave the pending job untouched.
  fi
fi
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
  set -f  # a path is a word, never a glob
  for f in $files; do
    # sq(): a route dir with \$param or a name with ' stays one literal path in the job script
    if [ -e "$wt/$f" ] || git -C "$wt" ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then kept="$kept $(sq "$f")"
    elif git -C "$wt" diff --cached --name-only --diff-filter=D | grep -qx "$f"; then echo "note: $f is an already-staged deletion, included via the index" >&2
    else echo "--files: $f is neither in the worktree nor tracked" >&2; exit 2; fi
  done
  set +f
  files=$kept
fi
# one staging rule: explicit paths are the norm (--files); `git add -A` is the fallback, always flagged so it is
# never mistaken for a deliberate choice — the incident that motivated this (signed-git-commits skill, handoff-incidents.md)
# was exactly a `git add -A` sweeping in a leftover file nobody meant to commit.
if [ -z "$files" ]; then
  if [ $all = 1 ]; then echo "enqueue.sh: --all — staging $wt with \`git add -A\`" >&2
  else echo "enqueue.sh: no --files/--all given — staging $wt with \`git add -A\` (pass --files for explicit paths, or --all to make this intentional)" >&2
  fi
fi
# overview metadata (ticket / epic / repo / PR / subject) for `make sign`. Not best-effort: a bad --ticket/--epic/--pr
# is a mistake worth stopping on, so a failed `meta` call fails the enqueue instead of silently queuing a job
# signq.py can only partly describe.
flags=""
[ $rebase = 1 ] && flags="$flags,rebase"; [ $newbr = 1 ] && flags="$flags,new-branch"
[ -n "$onto" ] && flags="$flags,onto"; [ -n "$lease" ] && flags="$flags,force-with-lease"; [ -n "$files" ] && flags="$flags,files"
[ $all = 1 ] && flags="$flags,all"
nfiles=-1
[ -n "$files" ] && nfiles=$(python3 -c 'import shlex,sys; print(len(shlex.split(sys.argv[1])))' "$files")
meta=$(python3 "$(dirname "$0")/signq.py" meta "$wt" "$br" "$msg" --topic "$topic" --by "$by" --ticket "$ticket" \
         --epic "$epic" --pr "$pr" --summary "$summary" --flags "${flags#,}" --files "$nfiles") \
  || { echo "enqueue.sh: signq.py meta failed (see above) — fix the flag it rejected and re-run" >&2; exit 2; }
mkdir -p "$Q"  # the workspace queue dir is created on first use (#7) — nothing ships or seeds it
job="$Q/$(date -u +%Y%m%dT%H%M%SZ)-$topic.sh"
tmp="$job.tmp"
{
  echo '#!/bin/sh'
  echo "# sign-queue job: $topic  (enqueued $(date -u +%FT%TZ) by session $by)"
  echo "# worktree $wt  branch $br"
  [ -n "$meta" ] && echo "# META $meta"
  echo 'set -eu'
  echo "WT=$(sq "$wt")"; echo "BR=$(sq "$br")"; echo "MSG=$(sq "$msg")"
  if [ -n "$files" ]; then echo "git -C \"\$WT\" add -- $files"; elif [ -n "$explicit_files" ]; then echo '# all listed files were already staged'; else echo 'git -C "$WT" add -A'; fi
  echo 'git -C "$WT" diff --cached --stat'
  echo '# commit only if something is staged (a retry after a failed push must not fail here)'
  echo 'if ! git -C "$WT" diff --cached --quiet; then git -C "$WT" commit -S -F "$MSG"; else echo "(already committed)"; fi'
  if [ $rebase = 1 ]; then
    echo 'git -C "$WT" fetch origin "$BR"'
    # FETCH_HEAD, not origin/$BR: a single-branch / partial clone (refspec = main only)
    # never creates refs/remotes/origin/<branch>, so origin/$BR is an "invalid upstream".
    # -f/--force-rebase: without it, a rebase whose upstream did not move since the branch was last
    # based on it is a no-op — git leaves every existing commit exactly as it is, sandbox-made ones
    # included, instead of replaying (and re-signing) them. -f always replays the range, so a commit
    # made unsigned in a sandbox comes out signed on this, the host side of the queue.
    echo 'git -C "$WT" rebase -S -f FETCH_HEAD'
  fi
  if [ -n "$onto" ]; then
    echo "UP=$(sq "$onto_br")"; echo "OLD_BASE=$(sq "$onto_base")"
    echo '# stacked branch: replay our commits on the upstream branch'"'"'s pushed tip, dropping the local placeholder'
    echo 'git -C "$WT" fetch origin "$UP"'
    # -f: same trap as --rebase above — a re-stack whose upstream tip has not moved since the last
    # enqueue is otherwise a no-op and leaves sandbox-made commits unsigned.
    echo 'git -C "$WT" rebase -S -f --onto FETCH_HEAD "$OLD_BASE"'
  fi
  # Belt and braces on top of -f above: verify every commit about to be pushed, not just HEAD (a
  # signed HEAD says nothing about the commits beneath it). OLDTIP is the remote tip the push is
  # landing on: FETCH_HEAD when a rebase/re-stack just ran (it fetched the relevant remote tip),
  # else the inspected --force-with-lease sha. A plain push / --new-branch has no such known tip to
  # diff against here and is left to the existing per-commit `commit -S` guarantee.
  if [ -n "$onto" ] || [ $rebase = 1 ]; then
    echo 'OLDTIP=$(git -C "$WT" rev-parse FETCH_HEAD)'
  elif [ -n "$lease" ]; then
    echo "OLDTIP=$(sq "$lease")"
  fi
  if [ -n "$onto" ] || [ $rebase = 1 ] || [ -n "$lease" ]; then
    echo 'unsigned=""'
    # revs on its own line: a `for … in $(cmd)` list does not propagate cmd'"'"'s exit under `set -e` (the shell
    # only checks the exit of the whole pipeline/expansion at the point of the simple command it feeds), so a
    # bad OLDTIP (e.g. an unfetched --force-with-lease sha) would otherwise empty the loop instead of failing.
    echo 'revs=$(git -C "$WT" log --format="%H:%G?" "$OLDTIP..HEAD")'
    echo 'for sc in $revs; do'
    echo '  sig=${sc##*:}'
    echo '  # N = no signature, B = bad one: never push those. G/U/E carry a signature (U = key not in the allowed'
    echo '  # signers, E = this host cannot verify it, e.g. no gpg.ssh.allowedSignersFile) — the drain reports which.'
    echo '  case "$sig" in N|B) unsigned="$unsigned ${sc%%:*}" ;; esac'
    echo 'done'
    echo '# never push a row the drain would have to report as unsigned: refuse and park the job instead'
    echo 'if [ -n "$unsigned" ]; then echo "UNSIGNED$unsigned"; exit 1; fi'
  fi
  if [ $newbr = 1 ]; then echo 'git -C "$WT" push -u origin "$BR"'
  elif [ -n "$lease" ]; then echo "LEASE=$(sq "$lease")"; echo '# rewritten history of an already-pushed branch: land only if the remote tip is still the one we inspected'; echo 'git -C "$WT" push --force-with-lease="refs/heads/$BR:$LEASE" origin "$BR"'
  else echo 'git -C "$WT" push origin "$BR"'; fi
  echo 'git -C "$WT" log --format="pushed %h %G? %s" -1'
} > "$tmp"
# --supersede's actual deletion: every check above (--onto, --force-with-lease, --files, signq.py meta) has
# now had its chance to `exit 2` — only past this point is the new job guaranteed to be installed, so only
# past this point is dropping the old one safe.
if [ -n "${cand:-}" ]; then
  rm -f "$cand"
  echo "enqueue.sh: --supersede dropped $cbase" >&2
fi
mv "$tmp" "$job"
[ -n "$meta" ] && printf '%s' "$meta" | python3 -c '
import json,sys; m=json.load(sys.stdin)
pr=("#%s" % m["pr"]) if m.get("pr") else "no PR yet"
print("queued %s: %s · epic %s · %s %s · %s" % (m.get("topic"), m.get("ticket") or "?", m.get("epic") or "?",
      (m.get("repo") or "?").split("/")[-1], pr, m.get("subject") or ""))' >&2
echo "$job"
