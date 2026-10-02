---
name: signed-git-commits
description: "Discipline behind signed/SSH commits where the editing machine cannot sign or push: worktree-first branching, exact staging, message-to-file, rebase quirks, gh-token prefix. Hand-off: `sign-queue`, never pasted git commands. Read when committing/pushing in a repo with a signed-commits ruleset (`systems.signed_commits`)."
compatibility: "Designed for Claude Code; needs signed_commits (systems.*)"
metadata:
  version: "12"
  updated: "2026-10-02"
  reviewed: "2026-10-02"
  requires: "signed_commits"
user-invocable: true
---

# Signed Git Commits — when the editing machine cannot sign

Where the machine a session edits on holds no signing key (a sandbox, a CI box, any machine
without the user's keyring), it cannot produce a valid signed commit and, for SSH-remote
repos, cannot push either. Both need the user's own machine — "the host" below. This skill is
the reliable pattern for getting changes from "written here" to "committed, signed, and pushed" without
corrupting the user's working tree or silently dropping files.

> **The hand-off itself goes through the `sign-queue` skill — you enqueue a job, the user
> drains the queue with one command. Never paste `git commit`/`git push` commands for
> the user to run** (their terminal mangles the multi-line/​wrapped git one-liners — that
> failure is *why* the queue exists). This skill is the **why + the discipline you must
> satisfy before enqueuing** (worktree isolation, exact staging, message-to-file, rebase
> gpg trap, `gh` token). Do the signing/push handoff via `sign-queue`.

> On a machine where `signed_commits` is false, print `signed-git-commits: not applicable here —
> signed_commits is false` and stop — commits are made and pushed directly.

## Why the editing machine cannot do this itself

- **Signing.** These repos set `commit.gpgsign=true` with a `user.signingkey` pointing
  at a key file (e.g. `~/.ssh/id_ed25519.pub`) that exists only on the user's machine, and a
  forwarded SSH agent, where there is one, holds zero identities here. Any `git commit` attempt fails with `Couldn't load public key`. If the repo
  enforces a signed-commits ruleset (check `gh api repos/<org>/<repo>/rulesets`),
  an unsigned commit literally cannot merge — don't fall back to `--no-gpg-sign` as
  a "fix", it just produces a commit that has to be re-signed later.
- **SSH push.** If `git remote -v` shows `git@github.com:...` (SSH, not HTTPS), push
  fails too — there's no `ssh-askpass` and no usable agent identity on this machine.
  This is a *different* failure from signing and needs the same fix (hand it to the
  host). Where a proxy injects HTTPS credentials (a sandbox), that covers HTTPS remotes
  and `gh`/`curl` API calls only; a raw SSH remote bypasses the HTTP proxy entirely, so
  it gets none of that.

## The pattern

1. **Work in a dedicated `git worktree`, never the shared checkout.** If the repo
   directory is a checkout the user also has open (a working tree mounted from their
   machine — one working tree, one HEAD; assume it is unless you cloned it yourself), any
   `git checkout -b`, `git switch`, or `git reset` you run moves what the user has
   open on their own screen, with zero warning to them. It has caused a force-push
   that reset a real branch to `main` and auto-closed a PR with review history on it.
   Before the first commit on a new branch: `git worktree add <path> [<branch>]`.
   Everything below happens inside that worktree — it has its own HEAD, so normal
   git commands are safe there.
2. **Stage exactly the files the commit should contain.** Use `git add <path> <path>`
   with explicit paths, never `git add -A`, and never assume the index is empty —
   staged changes accumulate across separate `git add` calls if nothing has been
   committed yet (incident: `reference/handoff-incidents.md` #3). Run
   `git diff --cached --stat` right before every commit and confirm it matches what
   you expect, every time — don't trust that staging state is what you last set it to.
   The same rule applies to the `sign-queue` hand-off: always pass one `--files <path>`
   per path you already verified, never leave both `--files` and `--all` off —
   that falls back to `add -A` on the host (flagged with a warning, but still not what you
   verified). Pass `--all` only on the rare job that really should carry everything.
3. **Write the commit message to a file, not an inline string.** Multi-paragraph
   messages explaining *why*, not just *what*, are the norm in these repos — an
   inline `-m` string is both unwieldy and easy to mis-paste. Write it to a file at an
   **absolute path OUTSIDE the worktree** (an absolute path under `<workspace root>/.worktrees/`,
   e.g. `$PWD/.worktrees/<TICKET>-<topic>-commit-msg.txt`) — sign-queue's default `add -A`
   would otherwise commit the message file itself. Pass that path to the `sign-queue`
   enqueue; `commit -S -F` runs on the host when the user drains the queue. Never hand
   the user the `git commit` line.
   The subject line follows the repo's **commit style** — Conventional Commits unless the repo
   overrides it (`commit_style.py resolve --dir <wt>`; the enqueue refuses a message that fails
   `commit_style.py check`). Write `feat(dbt): KEY-123 add …`, not `KEY-123: add …`, in a conventional repo.
4. **Every handoff starts with `cd <worktree path>`, even if you think they're already there.** The human is moving between worktrees across the conversation, not just running your commands in isolation — a handoff missing the `cd` risks running in whatever directory their shell happens to be sitting in. Make it the first line every time, not just for the first handoff.
5. **Only signing + SSH-push go to the host — and they go via `sign-queue`, not pasted
   commands.** Everything else — fetch, rebase, conflict resolution (edit the files,
   `git add` the resolution), branch pointer moves within your own worktree, staging —
   you do yourself. The host is reserved for the operations that create a
   signed commit object (`commit`, `commit --amend`, `rebase/cherry-pick/merge
   --continue`) and SSH-remote `push`. You don't hand those over as commands to paste:
   you **enqueue** the job (`sign-queue`'s `enqueue.sh`) and the user runs one `sign.sh`
   that drains the queue in order. Use `--rebase` on the job when the remote branch is
   ahead; a failed job is parked as `<job>.failed` and routed back to its owning session.
6. **Rebase has its own signing trap.** Once `git rebase` starts, the sign flag is frozen in
   `.git/rebase-merge/gpg_sign_opt` and config changes are ignored for the rest of that rebase — if
   `--continue` mysteriously doesn't ask for a signature, delete that file (emptying it still triggers
   signing) before continuing (`reference/handoff-incidents.md` #4).
7. **The paste hazard is exactly why we enqueue instead of handing over commands.** A chained `git commit
   --amend -S --no-edit && git push --force-with-lease` and a wrapped `git add <file1> <file2>` both broke
   in the user's terminal before (`reference/handoff-incidents.md` #2) — `sign-queue` removes the whole
   class of failure, since the job file is a fixed script, not a pasted line. The surviving discipline is
   on **your** side: run `git -C <worktree> status --short` and confirm it shows exactly the files (and
   count) the commit should carry *before* you enqueue.
8. **Where a proxy injects the credentials (a sandbox), `gh` needs a placeholder token.** `gh auth status`
   reads logged out there — the proxy injects credentials at the network layer, invisibly to `gh` itself
   (`reference/handoff-incidents.md` #5) — so `gh` refuses to run until it sees *some* token. Prefix every
   `gh` invocation with `github.sandbox_token_prefix`, or run `eval "$(python3
   $BATON/context-db/bin/kit_profile.py gh-env)"` once per shell (skill `gh-cli`); an empty prefix means
   `gh` is logged in natively and runs bare. Plain `curl` against `api.github.com` works the same way.

## Worked example (the shape this takes in practice)

Everything below happens on your side, inside a dedicated worktree. You do NOT hand
the user any git commands — you enqueue and they drain.

```
# 1. Stage exactly what the commit should carry, then verify:
git add <path/to/changed_file>
git -C <worktree> status --short      # must show exactly the intended file(s)
```
Write the full message to an absolute path under `<workspace root>/.worktrees/`, e.g. `$PWD/.worktrees/KEY-123-commit-msg.txt`
(outside the worktree), then enqueue via the `sign-queue` skill:
```
sh $BATON/skills/sign-queue/enqueue.sh \
   key-123-fix <abs-worktree> <branch> \
   $PWD/.worktrees/KEY-123-commit-msg.txt \
   --by <your-session> --files "<path/to/changed_file>"
```
Then tell the user one line: "queued `key-123-fix` — run the sign command when convenient."
Keep your `pr-watch` monitor armed; it reports the head move when the push lands.

**Rebase first (remote branch ahead):** you run the fetch/rebase/conflict-resolution
yourself, then enqueue with `--rebase` (the job does `fetch` + `rebase -S
origin/<branch>` on the host before pushing). Never paste a `git rebase --continue`/`push`
line for the user — that is what the queue is for.

## Related

- **`sign-queue` skill** — the operational hand-off this skill defers to: `enqueue.sh`
  (session side) + `sign.sh` (the user drains). This skill is the *why + discipline*;
  sign-queue is the *mechanism*. Always pair them.
- The incident history and the edge cases (shared-checkout HEAD-move recovery, the PR
  auto-close that came from it, the pasted one-liner failures) are `reference/handoff-incidents.md`
  beside this file — read it if something here doesn't cover the situation you're in.
- Repo-specific PR conventions (branch naming, required PR sections) are usually in
  that repo's own `CLAUDE.md` — this skill is only about the commit/push mechanics,
  not what should be in the PR.
