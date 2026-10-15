---
name: sign-queue
description: "Commits and pushes in a signed-commits repo from a process with no signing identity: worktree-first, exact staging, a message file, then a queued job the user drains on the host with `make sign`. Use when committing, pushing or rebasing there, or when asked why signing happens on the host; never paste git one-liners. Inert when `systems.signed_commits` is false."
compatibility: "Designed for Claude Code; needs signed_commits (systems.*)"
metadata:
  version: "25"
  updated: "2026-10-15"
  reviewed: "2026-10-15"
  requires: "signed_commits"
---

# sign-queue — sessions enqueue, the user drains

**The gate: this process has no signing identity.** `commit -S` needs the user's key, which only the user's
own machine ("the host") holds; an SSH `push` needs the same. So sessions **enqueue** a job and the user
**drains** the queue with `make sign`. Never paste git commands for the user to run — not a `commit`, not a
`push`, not a `cd`; the queue is the hand-off.

> On a machine where `signed_commits` is false, print `sign-queue: not applicable here — signed_commits is
> false` and stop — a commit is made and pushed the ordinary way instead. Every `make sign*` target carries the
> same gate (one line, exit 0); an env store that cannot be read stops with `sign-queue: systems.signed_commits
> could not be read`.

## Before enqueuing

`reference/discipline.md` is the why and the recovery for each step:

1. Work in your own `git worktree`, never a checkout the user has open.
2. `git -C <wt> status --short` shows exactly the files the commit should carry; pass each as `--files <path>`.
3. The message is a file at an absolute path under `<workspace root>/.worktrees/` — outside the worktree — and
   its subject follows the repo's commit style (`commit_style.py check` runs at enqueue).
4. Fetch, rebase and resolve conflicts yourself; the host only creates signed commits and pushes. Never
   enqueue mid-rebase — `enqueue.sh` refuses it.

## Enqueue

```
sh $BATON/skills/sign-queue/enqueue.sh <topic> <abs-worktree> <branch> <abs-msg-file> --by <session> \
   --files <path> [--files <path> ...] [--ticket <KEY>] [--pr <n>]
```

- `topic` is `[A-Za-z0-9._-]`; `--by` (or `SIGN_QUEUE_BY`) is required — a failed job is routed back by it alone.
- **`enqueue.sh` decides the push shape itself:** no `<branch>` on origin → `--new-branch` (`push -u`); the
  remote tip not in HEAD's history → `--rebase` (the job fetches and runs `rebase -S -f FETCH_HEAD`). It prints a
  `note:` line for each. Passing either flag forces it on.
- `--force-with-lease <remote-sha>` for an already-pushed branch whose history is rewritten; `--onto
  <upstream>:<placeholder-sha>` for a stacked branch; `--supersede` folds a fix into your own pending job of
  the same topic; `--all` stages everything on purpose. Spec: `reference/enqueue-flags.md`.
- `enqueue.sh` echoes `queued <topic>: <ticket> · epic <epic> · <repo> #<pr> · <subject>`. Tell the user one line:
  "queued `<topic>` — `make sign` when convenient." Keep `pr-watch` armed; it reports the push.

## After the drain

A failed job is parked as `<job>.failed`; the drain names it and its owning session. Read it (`make sign_show
JOB=n`, `make sign_log JOB=n`), fix the worktree, delete the `.failed` file and enqueue again — or ask the user
for `make sign_retry JOB=n` when the job itself was fine. A commit that was made but not pushed re-enqueues as a
push-only retry. A push GitHub refuses with `GH007` (`! [remote rejected]`, "push would publish a private email
address") is not a moved remote: the drain hints the fix — the no-reply `user.email`
(`<id>+<login>@users.noreply.github.com`) and `git commit --amend --reset-author --no-edit -S` on the host; a
rebase cannot change an author e-mail.

## User side

```
make sign                # drain everything, non-interactive
make sign_list           # overview only            make sign V=1         # every git line
make sign_show JOB=1     # one job's script         make sign_log JOB=1   # its last drain log
make sign_retry JOB=1    # un-park a .failed        make sign_drop JOB=1  # delete a job
```

Jobs run in enqueue order: stage → `commit -S` → optional `rebase -S` → verify every pushed commit is signed →
push. Output, locking and the job files: `reference/drain.md`. A `gh` call in any of this follows the `gh-cli`
skill.
