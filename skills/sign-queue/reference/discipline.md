# sign-queue — the discipline before a job, and the incidents behind it

Loaded on demand from `SKILL.md` § Before enqueuing. The gate is the one in `SKILL.md`: this process has no
signing identity. A repo that sets `commit.gpgsign=true` with a `user.signingkey` this process cannot load fails
every `git commit` with `Couldn't load public key`, and an SSH remote (`git@github.com:…`) refuses the push for
the same reason. Under a signed-commits ruleset (`gh api repos/<o>/<r>/rulesets`) an unsigned commit cannot
merge, so `--no-gpg-sign` is never a fix — it only makes a commit that has to be re-signed later.

## 1. Worktree first

Work in a dedicated `git worktree add <path> [<branch>]`, never in a checkout the user also has open: that
checkout has one HEAD, and a `checkout -b`, `switch` or `reset` you run there moves what is on the user's screen
without warning. You usually cannot see whether they have it open, so assume the checkout is shared unless you
cloned it yourself — never switch branches or reset in one you did not. Everything after the `worktree add`
happens inside the worktree, where ordinary git is safe.

## 2. Exact staging

Stage explicit paths and pass the same ones as `--files <path>`, one flag per path. Never assume the index is
empty — staged changes pile up across separate `git add` calls until something is committed. Right before every
enqueue, `git -C <wt> status --short` (and `git diff --cached --stat`) must show exactly the files, and count,
the commit should carry: the job commits what is staged, so a wrong index becomes a wrong commit with no later
step to catch it. `--all` only for the rare job that really should carry everything.

## 3. The message file, outside the worktree

Write the message — the why, not only the what — to a file at an absolute path under `<workspace root>/.worktrees/`
(e.g. `$PWD/.worktrees/KEY-123-commit-msg.txt`). Inside the worktree, an `add -A` fallback would commit the file
itself; `enqueue.sh` refuses that unless `--files` names the paths.

## 4. The commit-style gate

The subject follows the repo's commit style — Conventional Commits (`type(scope): KEY-123 description`) unless the
repo overrides it; `python3 $BATON/context-db/bin/commit_style.py resolve --dir <wt>` says which. `enqueue.sh`
runs `commit_style.py check` on the file and refuses the job otherwise (exit 1 of the tool = fix the message; any
other exit = the tool itself failed). `SIGN_QUEUE_SKIP_STYLE=1` is the deliberate one-off bypass.

## 5. Rebases

Fetch, rebase and conflict resolution are yours; the host is only for what creates a signed commit object
(`commit`, `commit --amend`, `rebase/cherry-pick/merge --continue`) and the push. Do not leave a rebase half-done
for the user to `--continue`: abort it and let the job rebase — `enqueue.sh` adds `--rebase` when the remote moved,
and the job runs `fetch origin <branch>` + `rebase -S -f FETCH_HEAD` (never `origin/<branch>`, which a
single-branch clone does not have). `enqueue.sh` refuses a worktree with `rebase-merge` or `rebase-apply` in its
git dir.

**The signing trap.** Once a rebase starts, its sign flag is frozen in `.git/rebase-merge/gpg_sign_opt`; config
changes are ignored until it ends, so a rebase started here (no key) stays unsigned however it is continued. That
is why it is never handed on: `git rebase --abort` and enqueue — the job starts its own rebase with `-S`.
A rebase whose upstream has not moved is a no-op that keeps old unsigned commits — hence the job's `-f`, and its
check of every commit before the push (`reference/enqueue-flags.md` § The flags, the no-op re-stack trap).

## 6. A parked job

A failed job is parked as `<job>.failed` and routed back to its `--by` session; the other jobs still run. Read it
(`make sign_show` / `make sign_log`, or the file), fix the worktree — resolve the rebase, or `rebase --abort` and
re-plan — delete the `.failed` file and enqueue again. When only the push failed (worktree clean, HEAD ahead of the
remote), enqueue the same job again: the commit step is skipped. When the job itself was fine (network, a remote
fixed meanwhile), ask the user for `make sign_retry JOB=n`.

## Incidents

**A branch switch in a shared checkout moved the user's HEAD.** A session ran `checkout -b` / `reset` in a checkout
the user also had open; their branch changed with no warning, a later push force-updated a real branch to the
base branch, and GitHub auto-closed the PR it carried, review history included. Recovery: push nothing;
`git -C <checkout> reflog` shows the HEAD they had; restore it with `git -C <checkout> switch <their-branch>`, or
`reset --keep <reflog-sha>` when the pointer itself moved (`--keep` refuses to overwrite local changes); if a
remote branch was force-updated, enqueue the restoring push with `--force-with-lease <wrong-sha>` onto the good
sha, then `gh pr reopen <n>` (the review threads come back); tell the user what moved and what you restored, in
one message, before any other work.

**Pasted git one-liners broke in the user's terminal.** `git commit --amend -S --no-edit && git push
--force-with-lease` had its second half run as its own command; a wrapped `git add <file1> <file2>` dropped its
second path, so the commit carried one of two files. Owner decision, 2026-09-10: one queue all sessions add to,
cleaned on a successful sign — the job file is a fixed script, so nothing is re-typed. That is also why no
hand-off ever starts with a `cd` line or any other command for the user: the only thing they run is `make sign`.

**The index carried more than the commit was meant to.** `git add a` for change A, later `git add b` for change B,
then a commit with A's message took both; the follow-up `git add -A` swept unrelated scratch files into B. Hence
explicit `--files` and the `status --short` check (§ 2), and the `add -A` warning in `enqueue.sh`.

**A rebase that started unsigned stayed unsigned.** § 5's trap: the frozen `gpg_sign_opt`, and the no-op re-stack
that leaves earlier commits as they were.

`gh` calls around this (PR state, branch tips, rulesets) follow the `gh-cli` skill, including how this machine
authenticates.
