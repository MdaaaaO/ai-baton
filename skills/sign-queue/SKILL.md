---
name: sign-queue
description: "Shared queue for commits that must be GPG/SSH-signed and pushed by the user on the host: a session enqueues a job (worktree, branch, message file, flags), the user drains it with one command. Use for every commit or push in a signed-commits repo; never paste git one-liners. Inert where `systems.signed_commits` is false."
compatibility: "Designed for Claude Code; needs signed_commits (systems.*)"
metadata:
  version: "23"
  updated: "2026-10-01"
  reviewed: "2026-09-27"
  requires: "signed_commits"
---

# sign-queue — one command for the user, a queue for every session

Signing happens on the user's machine — "the host" (`commit -S` fails wherever the signing key is absent) — and long chained one-liners
break on the user's terminal line wrap. So: sessions **enqueue**, the user **drains**.

> On a machine where `signed_commits` is false, print `sign-queue: not applicable here — signed_commits is
> false` and stop — a commit is made and pushed the ordinary way instead. The host side has the same gate:
> every `make sign*` target prints that line and exits 0 without touching `signq.py` when `signed_commits`
> is false, so a stray `make sign` on a machine with no signing key is harmless. An env store that cannot
> be read is not "false": the target stops with `sign-queue: systems.signed_commits could not be read`.

## Session side — enqueue a job

```
sh $BATON/skills/sign-queue/enqueue.sh <topic> <abs-worktree> <branch> <abs-msg-file> --by <session> [--rebase] [--new-branch] [--files <path> [--files <path> ...] | --files "<paths>" | --all] [--onto <upstream-branch>:<old-base-sha>]
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
- `--force-with-lease <remote-sha>` whenever an **already-pushed** branch is about to run `--onto`/`--rebase`
  again, or gets a fixup — not only "the upstream moved": the job's `-f` (see the no-op re-stack trap below)
  always replays and re-signs the whole range, so every commit gets a new sha even when the upstream tip has
  not moved since the last re-stack, and the plain push is rejected as non-fast-forward either way. The job pushes `--force-with-lease=refs/heads/<branch>:<remote-sha>`,
  so it lands only if the remote tip is still the sha you read via `gh api repos/<o>/<r>/branches/<branch>`;
  a moved remote parks the job as `.failed` instead of clobbering someone's push. Exclusive with `--new-branch`.
- `--onto <upstream-branch>:<old-base-sha>` for a **stacked** branch whose upstream is itself unsigned/unpushed
  locally: branch the stack at the last pushed commit, make an *unsigned local placeholder commit* holding the
  upstream's tree, build on top, then enqueue with `--onto <upstream>:<placeholder-sha>`. The job commits, fetches
  the upstream's remote tip and runs `rebase -S --onto FETCH_HEAD <placeholder-sha>`, so only your commits land on
  the real upstream and the placeholder is dropped. Enqueue the upstream's job first (same drain is fine — jobs run
  in enqueue order); if that job fails, this one fails on the fetch and is parked, nothing is pushed on a stale base.
  Exclusive with `--rebase`.
  - **The no-op re-stack trap:** if the upstream tip has not moved since your own worktree was last rebased onto
    it (wherever that ran — signing key absent, so no `-S`), a plain `rebase --onto`/`--rebase` is a no-op: git
    leaves every existing commit exactly as it is instead of replaying it, so that earlier unsigned commit stays
    unsigned even though the job reports a signed HEAD. The job always force-rebases (`-S -f`) so the range is
    replayed — and re-signed — every time, and it verifies every commit about to be pushed (not just HEAD) before
    pushing at all, refusing (parked, `UNSIGNED <sha>`) rather than land one with no signature or a bad one
    (`%G?` N or B; U and E carry a signature this host cannot fully trust or check, and the drain reports them).
- **One staging rule, same as `signed-git-commits`: stage explicit paths, never a silent `add -A`.**
  Repeat `--files <path>` once per path — the norm for more than one path; a repeated path is never
  split. Every path is literal, never a glob or git pathspec magic (`a*.txt` is the file of that name).
  Given exactly once, the legacy space-separated form still works for existing callers: `--files "a b"`
  is staged as one path when `a b` already exists in the worktree or is tracked as such, otherwise it is
  split on whitespace into `a` and `b` (the old behaviour) — so a list of several paths that happen to contain a space needs one `--files` per path, not
  one `--files` with all of them. Each kept path is single-quoted in the job: route dirs of some web
  frameworks contain `$param`, and the unquoted form aborts at `set -u` ("param: unbound variable") before
  any git runs — re-enqueue is the fix.
  `--all` stages with `git add -A` **on purpose** (mutually exclusive with `--files`) — pass it when you
  mean to carry everything. Passing neither still falls back to `add -A` for a plain retry, but `enqueue.sh`
  prints a warning either way: `-A` is never a silent default.
- `--by <your session name>` (as ListAgents shows it) — **required**: a call with neither `--by` nor
  `SIGN_QUEUE_BY` set exits 2 with a usage message instead of queuing an unowned job. There is no fallback
  to a workspace identity — a failed job is routed back to its owner by this value alone.
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
  worktree, otherwise a fallback `add -A` (default or `--all`) would commit the message file itself; `enqueue.sh` refuses
  that unless `--files` names the paths explicitly.
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

### Folding fixes into an unsigned job

A review round (fix → enqueue → wait for `make sign` → reply) that gets another round of fixes before the
owner drains would otherwise mean two jobs for one topic — or a hand check of the log/`.failed` state
before deleting the old `.sh` yourself. `--supersede` does this in one step: it finds the newest PENDING
job with the same `topic` (and, when you also pass `--by`, the same `--by`), deletes it, prints which job
it dropped, then enqueues as usual. It refuses (exit 2, nothing touched) when that job already has a drain
log or is parked as `<job>.failed` — it was attempted, not just sitting in the queue, and folding into it
would lose that history; resolve it (`make sign_show` / `sign_log` / `sign_retry`) first instead. No match
for the topic (and `--by`) is not an error: the job just enqueues normally, same as without the flag.
**Folding does not merge the dropped job's own content** — its `--files`, message file and
`--rebase`/`--onto`/`--force-with-lease` flags are gone with it, not carried onto the new enqueue. Pass the
union of both rounds' `--files` (and whichever of those flags the earlier round needed) on the new call, or
the earlier round's paths are left uncommitted and never pushed.

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

Only one drain runs at a time: `make sign` holds an exclusive lock on the queue for the whole run, so a second
`make sign` started while the first is still going prints one line naming the holder — "another drain (pid
1234, on key-123-p4) already holds the lock — exiting", or "another sign-queue process" when the lock file
names no live drain — and exits 0 immediately, never a half-drained overlap. A job file removed by hand
while a drain is already in progress is skipped with one line; the rest of the drain and its summary still run.

Jobs a pre-workspace-queue kit left under the kit dir are moved under that same lock, and only into the queue
of the workspace the kit sits in (a kit that drains another workspace's queue leaves them alone): `run` moves
them itself before it loads a job (`make sign` calls `run` only; a `--dry-run` moves nothing), and `make
sign_list` runs `signq.py migrate-legacy` first, which takes the lock for the move; while a drain holds it,
it moves nothing and says so.

A job stays in the drain's terminal session — signing may need the terminal for a passphrase, and Ctrl-C
stops the drain and its job together. A drain that is killed outright (`kill -9`) can still leave its job
running, so the lock file records the holder's pid and the job it is on. The next `run` that finds that job's
process alive (same pid and same start time by `ps -o lstart=`, read in UTC, so neither a reused pid nor a
zombie counts) names the job and its pid and exits 2 without starting a job; once that process is gone, `run`
drains as usual. An exit 2 for a pid that `ps -p <pid>` shows is not that job: delete `<queue>/.lock` while
no drain runs, then `make sign` again. Not covered: two machines sharing one queue directory.

## Why

Owner decision, 2026-09-10: "the git one line message doesn't work anymore, i can't copy paste it … a sandbox shell
mangles the line breaks" and "one script for all sessions … like a queue where other sessions can add if they
need something signed … cleans it on successful sign". The history of the one-liner failures is the
`signed-git-commits` skill's reference file on hand-off incidents (`handoff-incidents.md` beside its SKILL.md).
