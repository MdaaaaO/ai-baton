# sign-queue — the enqueue flags in full

Loaded on demand from `SKILL.md` § Enqueue. Full signature:

```
sh $BATON/skills/sign-queue/enqueue.sh <topic> <abs-worktree> <branch> <abs-msg-file> --by <session> [--rebase] [--new-branch] [--files <path> [--files <path> ...] | --files "<paths>" | --all] [--onto <upstream-branch>:<old-base-sha>] [--force-with-lease <remote-sha>] [--supersede] [--ticket <KEY>] [--epic <KEY>] [--pr <n>] [--summary "<text>"]
```

## What `enqueue.sh` decides itself

It reads `refs/heads/<branch>` on `origin` once (`git ls-remote`; a lookup that fails stops the enqueue with
git's own error, never read as "no branch"):

| remote state | added | unless |
|---|---|---|
| no `<branch>` on origin | `--new-branch` (`push -u`) | `--force-with-lease` given |
| tip not in HEAD's history (`merge-base --is-ancestor` false, or the tip is not even fetched here) | `--rebase` | `--onto` or `--force-with-lease` given — both rewrite history on purpose |
| a `rebase-merge` / `rebase-apply` dir in the worktree's git dir | — refused, exit 2 | never |

A flag passed explicitly is always kept (forced on). A worktree with no `origin` remote at all decides
nothing. A clean worktree whose HEAD is ahead of the remote tip becomes a push-only retry (the commit step is
skipped as "already committed").

## The flags

- `--rebase` — the job fetches `<branch>` and runs `rebase -S -f FETCH_HEAD` before pushing (not
  `origin/<branch>`: a single-branch clone never creates that ref).
- `--new-branch` — first push (`push -u`). Exclusive with `--force-with-lease`.
- `--force-with-lease <remote-sha>` — whenever an **already-pushed** branch is about to run `--onto`/`--rebase`
  again, or gets a fixup: the job's `-f` always replays and re-signs the whole range, so every commit gets a new
  sha and a plain push is rejected as non-fast-forward. The job pushes
  `--force-with-lease=refs/heads/<branch>:<remote-sha>`, so it lands only if the remote tip is still the sha you
  read (`gh api repos/<o>/<r>/branches/<branch> -q .commit.sha`); a moved remote parks the job as `.failed`.
- `--onto <upstream-branch>:<old-base-sha>` — a **stacked** branch whose upstream is itself not pushed yet:
  branch the stack at the last pushed commit, make an *unsigned local placeholder commit* holding the upstream's
  tree, build on top, then enqueue with `--onto <upstream>:<placeholder-sha>`. The job commits, fetches the
  upstream's remote tip and runs `rebase -S -f --onto FETCH_HEAD <placeholder-sha>`, so only your commits land
  and the placeholder is dropped. Enqueue the upstream's job first (jobs run in enqueue order); if it fails,
  this one fails on the fetch and nothing is pushed on a stale base. Exclusive with `--rebase`.
  - **The no-op re-stack trap:** when the upstream tip has not moved since the worktree was last rebased onto
    it, a plain `rebase --onto` is a no-op and leaves the earlier unsigned commits unsigned. The job always
    force-rebases (`-S -f`) and then verifies every commit about to be pushed (`%G?`), refusing — parked,
    `UNSIGNED <sha>` — on N (none) or B (bad); U and E carry a signature this host cannot fully check, and the
    drain reports them.
- `--files <path>` — one literal path per flag (never a glob or pathspec magic; `a*.txt` is that file). Given
  exactly once, the legacy space-separated form still works: `--files "a b"` is one path when `a b` exists or is
  tracked, otherwise split into `a` and `b`. A `git rm`-staged deletion is dropped from the add line (already in
  the commit); a path neither present nor tracked is refused. Every path is single-quoted in the job (route
  dirs with `$param` would otherwise abort at `set -u`).
- `--all` — `git add -A` on purpose; exclusive with `--files`. Neither flag still falls back to `add -A`, with a
  warning: `-A` is never a silent default. A message file inside the worktree is refused unless `--files` names
  the paths.
- `--by <session>` — required (or `SIGN_QUEUE_BY`); no fallback to a workspace identity.
- `--ticket` / `--epic` / `--pr` / `--summary` — the overview columns of `make sign`, all optional and derived:
  ticket from the branch/topic, epic from the `.context/` doc that mentions the ticket, PR via `gh pr list
  --head <branch>`, summary = the message file's first line. Pass `--pr` when no PR is open yet or `gh` is
  absent. The PR column tells apart `no PR yet` (gh answered: none), `PR lookup failed (<reason>)` and
  `no gh → pass --pr`.
- `SIGN_QUEUE_SKIP_STYLE=1` — the deliberate one-off bypass of the commit-style check; say so when you use it.

## Folding fixes into an unsigned job — `--supersede`

A second review round before the owner drained the first would otherwise mean two jobs for one topic.
`--supersede` deletes the newest PENDING job with the same `topic` (and the same `--by`, when given), prints
which, then enqueues as usual. It refuses (exit 2, nothing touched) when that job already has a drain log or a
parked `.failed` — resolve it with `make sign_show` / `sign_log` / `sign_retry` first. No match is not an error.
**Folding does not merge the dropped job's content**: pass the union of both rounds' `--files` (and whichever
of `--rebase`/`--onto`/`--force-with-lease` the earlier round needed), or the earlier paths are never committed.
