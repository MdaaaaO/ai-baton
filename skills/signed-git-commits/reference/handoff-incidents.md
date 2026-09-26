# signed-git-commits — incident history and edge cases

What went wrong before the pattern in `SKILL.md` existed, in general form (no machine, no repository, no
person), and the recovery steps for the cases the body only names. Loaded on demand.

## 1. A branch switch in a shared checkout moved the user's HEAD

**What happened.** The repository directory was a working tree the user also had open on their own machine
(a mounted checkout: one working tree, one HEAD). A session ran `git checkout -b` / `git reset` there to start
its work. On the user's screen the branch they were on changed with no warning; a later push from that state
force-updated a real branch to the base branch and GitHub auto-closed the open PR that branch carried, review
history included.

**Rule.** Never switch branches or reset in a checkout you did not clone yourself: `git worktree add <path>
[<branch>]` first, everything after that inside the worktree (SKILL.md § The pattern, step 1).

**Recovery when it has already happened.**

1. Stop; push nothing. `git -C <checkout> reflog` shows the HEAD the user had — the entry before your
   `checkout` / `reset`.
2. Restore it without touching their files: `git -C <checkout> switch <their-branch>`, or `git -C <checkout>
   reset --keep <reflog-sha>` when the branch pointer itself moved — `--keep` refuses to overwrite local
   modifications, which is the point.
3. If a remote branch was force-updated: enqueue the restoring push through `sign-queue` (the user's machine
   holds the credentials) as `--force-with-lease <wrong-sha>` onto `<good-sha>`, then `gh pr reopen <n>` —
   GitHub re-attaches the PR to the restored branch and the review threads are still there.
4. Tell the user exactly what moved and what you restored, in one message, before any other work.

## 2. Pasted git one-liners broke in the terminal

**What happened.** Commands handed to the user to paste — `git commit --amend -S --no-edit && git push
--force-with-lease`, a `git add <file1> <file2>` that wrapped — were executed in part: the second half of a
chained command was read as its own command; a wrapped line dropped its second path, so the commit silently
carried one of two files.

**Rule.** No pasted git. The session enqueues a job (`sign-queue`), the user drains the queue with one
`make sign`; the job file is a fixed script, so nothing is re-typed. The discipline left on the session's side:
`git -C <worktree> status --short` shows exactly the files the commit should carry before you enqueue.

## 3. The index carried more than the commit was meant to

`git add a` for change A, later `git add b` for change B, then `git commit -F msgA` committed both under A's
message; the follow-up `git add -A` swept unrelated scratch files into B.

**Rule.** `git diff --cached --stat` right before every commit; explicit paths, never `-A`, in a worktree that
may hold more than one change.

## 4. A rebase that started unsigned stays unsigned

`.git/rebase-merge/gpg_sign_opt` freezes the sign flag when the rebase starts; config changes are ignored until
it ends.

**Rule.** If `--continue` does not ask for a signature, remove that file (emptying it is not enough) before
continuing.

## 5. `gh` refused to run behind a credential-injecting proxy

Where a proxy adds the GitHub credential at the network layer (a sandbox), `gh auth status` says logged out and
`gh` refuses every command until it sees some token.

**Rule.** The environment's placeholder is `github.sandbox_token_prefix`; apply it with `eval "$(python3
$BATON/context-db/bin/kit_profile.py gh-env)"` — empty where `gh` is logged in natively, and then `gh` runs
bare (skill `gh-cli`).
