---
name: sign-queue
description: "Shared queue for commits that must be signed and pushed by the user on the host: a session enqueues a job (worktree, branch, message file, flags), the user drains it with one command. Use when committing or pushing in a signed-commits repo; never paste git one-liners. Inert when `systems.signed_commits` is false."
compatibility: "Designed for Claude Code; needs signed_commits (systems.*)"
metadata:
  version: "23"
  updated: "2026-10-02"
  reviewed: "2026-10-02"
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
- `--onto <upstream-branch>:<old-base-sha>` for a **stacked** branch whose upstream is itself
  unsigned/unpushed locally — branches at the last pushed commit via an unsigned local placeholder, the
  job force-rebases (`-S -f`) and re-verifies every commit before pushing (the no-op re-stack trap).
  Exclusive with `--rebase`. Full mechanism and the trap: `reference/flags.md`.
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
- `--ticket <KEY>` / `--epic <KEY>` / `--pr <n>` / `--summary "<text>"` — the overview columns `make sign`
  shows. All optional and auto-derived: ticket from the branch/topic, epic from the `.context/` epic doc
  that mentions the ticket, PR via `gh pr list --head <branch>`, summary = first line of the message file.
  Pass `--epic` when the ticket has no context doc yet and `--pr` when the PR is not open yet or `gh` is
  absent (the PR column then reads `new branch → no PR yet` / `PR lookup failed (<reason>)` / `no gh →
  pass --pr`). `enqueue.sh` echoes the resulting row (`queued <topic>: <ticket> · epic <epic> · <repo>
  #<pr> · <subject>`) — repeat that line to the user, nothing more.
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

`--supersede` folds a new fix round into the newest PENDING job with the same `topic` (and `--by`, when
passed) in one step — deletes it, prints which job it dropped, then enqueues as usual; refuses (exit 2)
when that job already drained or is `.failed` (resolve it first instead). No match is not an error, it
just enqueues normally. **It does not merge the dropped job's content** — pass the union of both rounds'
`--files` (and flags) on the new call, or the earlier round's paths are left uncommitted. Full detail:
`reference/flags.md`.

## User side — drain

```
make sign                # from the workspace root on the host — drains everything, non-interactive
make sign_list           # overview only, runs nothing
make sign_show JOB=1     # metadata + script of one job     make sign_log JOB=1    # its last drain log
make sign_retry JOB=1    # un-park a .failed as is           make sign_drop JOB=1   # delete a job
make sign V=1            # stream every git line instead of the milestones
```

All targets wrap `$BATON/skills/sign-queue/signq.py` (stdlib python3; `sign.sh` is a compatibility shim). A
drain prints the **overview table** (`# · TICKET · EPIC · REPO · PR · BRANCH · COMMIT · STAGE · BY`), one
**card per job** (ticket · epic · repo #PR · subject · branch · the `stage → commit -S → [rebase] → push`
chain), milestones as they happen (`committed <sha>`, `rebased`, `pushed a..b`), then `✔ pushed <sha> signed
✓` or `✘ FAILED … → parked` with a diagnosis hint and the last output lines, and a closing **summary** line
per job with its PR link. Git runs non-interactively (`GIT_PAGER=cat`, `GIT_EDITOR=true`,
`GIT_SEQUENCE_EDITOR=true`, `GIT_TERMINAL_PROMPT=0` — nothing waits for `q`/`:wq`); full per-job output:
`.context/state/sign-queue/logs/<job>.log`.

Jobs run in enqueue order: stage → `commit -S -F <msg>` (skipped if nothing staged, so a retry after a
failed push works) → optional `fetch` + `rebase -S` → `push`. Success deletes the job; a failure parks it
as `<job>.failed` and the drain continues, naming the failed topics and owning sessions in the summary and
exiting 1. The owning session inspects it (`make sign_show`/`sign_log`, or reads the `.failed` file), fixes
the worktree, and either re-enqueues (delete `.failed` first) or asks for `make sign_retry` when the job
itself was fine (transient network, remote fixed meanwhile).

Only one drain runs at a time: `make sign` holds an exclusive queue lock for the whole run, so a second
`make sign` started while the first runs prints one line naming the holder ("another drain (pid 1234, on
key-123-p4) already holds the lock — exiting", or "another sign-queue process" when the lock names no live
drain) and exits 0 — never a half-drained overlap. A job file removed by hand mid-drain is skipped with one
line; the rest of the drain and its summary still run.

A pre-workspace-queue kit's leftover jobs migrate under the same lock into the right workspace's queue
(`run` and `make sign_list` both trigger it). A job stays in the drain's terminal session for a passphrase
prompt; a `kill -9`'d drain can leave its job running, so the lock file records the holder's pid and job,
and the next `run` detects a still-live process and refuses to double-start it. Full mechanism — the
workspace-ownership rule, the `--dry-run`/migrate-legacy triggers, the pid/start-time detection and the
stale-lock recovery command: `reference/flags.md`.

## Why

Owner decision, 2026-09-10: "the git one line message doesn't work anymore, i can't copy paste it … a sandbox shell
mangles the line breaks" and "one script for all sessions … like a queue where other sessions can add if they
need something signed … cleans it on successful sign". The history of the one-liner failures is the
`signed-git-commits` skill's reference file on hand-off incidents (`handoff-incidents.md` beside its SKILL.md).
