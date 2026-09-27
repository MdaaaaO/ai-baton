---
name: sign-queue
description: Shared queue for commits that must be GPG/SSH-signed and pushed by the user on the host: a session enqueues a job (worktree, branch, message file, flags), the user drains the queue with one command. Use for every commit or push in a signed-commits repo; never paste git one-liners for the user to run. Inert where `systems.signed_commits` is false.
compatibility: "Designed for Claude Code; needs signed_commits (systems.*)"
metadata:
  version: "13"
  updated: "2026-09-27"
  reviewed: "2026-09-24"
  requires: "signed_commits"
---

# sign-queue — one command for the user, a queue for every session

Signing happens on the user's machine — "the host" (`commit -S` fails wherever the signing key is absent) — and long chained one-liners
break on the user's terminal line wrap. So: sessions **enqueue**, the user **drains**.

## Session side — enqueue a job

```
sh $BATON/skills/sign-queue/enqueue.sh <topic> <abs-worktree> <branch> <abs-msg-file> --by <session> [--rebase] [--new-branch] [--files "<paths>"] [--onto <upstream-branch>:<old-base-sha>]
```

- `topic` names the job (`key-123-p4`, `kb-round4`); `[A-Za-z0-9._-]` only.
- the message file's **subject follows the repo's commit style** — Conventional Commits (`type(scope): KEY-123
  description`) unless the repo overrides it (`pr-open` § Commit style; `python3 $BATON/context-db/bin/commit_style.py
  resolve --dir <wt>` tells you which). `enqueue.sh` runs `commit_style.py check` on the file and refuses the job
  otherwise; `SIGN_QUEUE_SKIP_STYLE=1` is the deliberate one-off bypass — say so when you use it.
- `--rebase` when the remote branch is ahead (GitHub "Update branch" merge commit, someone else pushed):
  the job fetches and `rebase -S FETCH_HEAD` before pushing (not `origin/<branch>` — a single-branch
  clone never creates that ref). Check with
  `gh api repos/<o>/<r>/branches/<branch> -q .commit.sha` vs local `git rev-parse HEAD` before enqueuing.
- `--new-branch` for the first push (`push -u`).
- `--force-with-lease <remote-sha>` when an **already-pushed** branch gets its history rewritten — a second
  `--onto` re-stack after the upstream branch moved, a fixup — the plain push is rejected as non-fast-forward. The job pushes `--force-with-lease=refs/heads/<branch>:<remote-sha>`,
  so it lands only if the remote tip is still the sha you read via `gh api repos/<o>/<r>/branches/<branch>`;
  a moved remote parks the job as `.failed` instead of clobbering someone's push. Exclusive with `--new-branch`.
- `--onto <upstream-branch>:<old-base-sha>` for a **stacked** branch whose upstream is itself unsigned/unpushed
  locally: branch the stack at the last pushed commit, make an *unsigned local placeholder commit* holding the
  upstream's tree, build on top, then enqueue with `--onto <upstream>:<placeholder-sha>`. The job commits, fetches
  the upstream's remote tip and runs `rebase -S --onto FETCH_HEAD <placeholder-sha>`, so only your commits land on
  the real upstream and the placeholder is dropped. Enqueue the upstream's job first (same drain is fine — jobs run
  in enqueue order); if that job fails, this one fails on the fetch and is parked, nothing is pushed on a stale base.
  Exclusive with `--rebase`.
- `--files "a b"` to stage only those paths instead of `add -A` (when the worktree has unrelated noise).
  Each path is single-quoted in the job: route dirs of some web frameworks contain `$param`, and the
  unquoted form aborts at `set -u` ("param: unbound variable") before any git runs — re-enqueue is the fix.
- `--by <your session name>` (as ListAgents shows it) — **always pass it**: a failed job is routed back to its owner by this.
- `--ticket <KEY>` / `--epic <KEY>` / `--pr <n>` / `--summary "<text>"` — the overview columns the user sees in `make sign`
  (added 2026-09-22, `signq.py`). All optional and auto-derived: ticket from the branch/topic, epic from the `.context/`
  epic doc that mentions the ticket, PR via `gh pr list --head <branch>`, summary = first line of the message file.
  The PR column distinguishes three non-PR outcomes: `—` / `new branch → no PR yet` (gh answered: none open),
  `PR lookup failed (<reason>)` (gh errored — auth, network, timeout, bad output; `show` carries the reason) and
  `no gh → pass --pr` (gh not installed on this machine — a documented skip, not a failure).
  Pass `--epic` when the ticket has no context doc yet and `--pr` when the PR is not open yet (a `--new-branch` job
  cannot find one) or when `gh` is absent. enqueue.sh embeds them as one `# META {…}` line in the job and echoes the resulting row
  (`queued <topic>: <ticket> · epic <epic> · <repo> #<pr> · <subject>`) — repeat that line to the user, nothing more.
- Before enqueuing: `git -C <wt> status --short` must show exactly the files the commit should carry;
  the message file must exist at an absolute path under `<workspace root>/.worktrees/`, e.g. `$PWD/.worktrees/KEY-123-commit-msg.txt` — OUTSIDE the
  worktree, otherwise the default `add -A` commits the message file itself (enqueue.sh refuses that unless `--files` is given).
- Then tell the user in one line: "queued `<topic>` — `make sign` when convenient." Do NOT paste
  git commands. Keep your `pr-watch` monitor armed; it reports the head move when the push lands.
- Job files are plain sh under `.context/state/sign-queue/` (in the workspace, so a plugin update never deletes them); `python3 …/signq.py list`
  (or `make sign_list` on the host) shows what is pending with ticket / epic / PR. A failed job is parked as `<job>.failed` (the user gets told which) and the other
  jobs still run; fix the worktree (e.g. resolve the rebase, or `rebase --abort` and re-plan), delete the
  `.failed` file and enqueue again. If the commit itself was made but only the **push** failed (worktree clean,
  HEAD ahead of `origin/<branch>`), enqueue the same job again — the commit step is skipped ("already committed");
  add `--rebase` when the remote moved (an "Update branch" merge commit is the usual cause of a rejected push).
- `--files` and deletions: a `git rm`-staged deletion is in neither worktree nor index, so `git add -- <path>`
  fails on it; enqueue.sh drops such paths (they are already in the commit) and refuses paths that are
  neither present nor tracked. Check `git status --short` before enqueuing (first column = already staged).

## User side — drain

```
make sign                # from the workspace root on the host — drains everything, non-interactive
make sign_list           # overview only, runs nothing
make sign_show JOB=1     # metadata + script of one job     make sign_log JOB=1    # its last drain log
make sign_retry JOB=1    # un-park a .failed as is           make sign_drop JOB=1   # delete a job
make sign V=1            # stream every git line instead of the milestones
```

All targets wrap `$BATON/skills/sign-queue/signq.py` (stdlib python3; `sign.sh` is a compatibility shim). A drain prints
the **overview table** (`# · TICKET · EPIC · REPO · PR · BRANCH · COMMIT · STAGE · BY`), then one **card per job**
(ticket · epic title · repo #PR, the commit subject, branch and the step chain `stage → commit -S → [rebase] → push`),
the milestones as they happen (`committed <sha>`, `rebased`, `pushed a..b`) and a `✔ pushed <sha> signed ✓` or
`✘ FAILED … → parked` line with a diagnosis hint and the last output lines, and finally a **summary** with one line
per job and its PR link. Git runs with `GIT_PAGER=cat`, `GIT_EDITOR=true`, `GIT_SEQUENCE_EDITOR=true`,
`GIT_TERMINAL_PROMPT=0`, so nothing ever waits for a `q` or `:wq`. Full per-job output is kept in
`.context/state/sign-queue/logs/<job>.log`.

Jobs run in enqueue order: stage → `commit -S -F <msg>` (skipped if nothing is staged, so a retry after a failed
push works) → optional `fetch` + `rebase -S` → `push`. Success deletes the job. A failure parks it as `<job>.failed`
and the drain continues with the next job; the summary names the failed topics and their owning sessions, and the
exit status is 1. The owning session inspects it (`make sign_show` / `make sign_log`, or read the `.failed` file),
fixes the worktree, and either re-enqueues (delete the `.failed` first) or asks the user for `make sign_retry` when
the job itself was fine (transient network, remote fixed meanwhile).

## Why

Owner decision, 2026-09-10: "the git one line message doesn't work anymore, i can't copy paste it … a sandbox shell
mangles the line breaks" and "one script for all sessions … like a queue where other sessions can add if they
need something signed … cleans it on successful sign". The history of the one-liner failures is the
`signed-git-commits` skill's reference file on hand-off incidents (`handoff-incidents.md` beside its SKILL.md).
