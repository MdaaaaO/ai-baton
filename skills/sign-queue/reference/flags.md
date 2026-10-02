# sign-queue — stacked branches and folding, full detail

Loaded from `SKILL.md` § Session side — enqueue a job. The full text moved out of the body for two
less-common cases: a stacked branch (`--onto`) and folding a fix round into an unsuperseded job
(`--supersede`).

## `--onto <upstream-branch>:<old-base-sha>` — a stacked branch

For a **stacked** branch whose upstream is itself unsigned/unpushed locally: branch the stack at the
last pushed commit, make an *unsigned local placeholder commit* holding the upstream's tree, build on
top, then enqueue with `--onto <upstream>:<placeholder-sha>`. The job commits, fetches the upstream's
remote tip and runs `rebase -S --onto FETCH_HEAD <placeholder-sha>`, so only your commits land on the
real upstream and the placeholder is dropped. Enqueue the upstream's job first (same drain is fine —
jobs run in enqueue order); if that job fails, this one fails on the fetch and is parked, nothing is
pushed on a stale base. Exclusive with `--rebase`.

**The no-op re-stack trap:** if the upstream tip has not moved since your own worktree was last rebased
onto it (wherever that ran — signing key absent, so no `-S`), a plain `rebase --onto`/`--rebase` is a
no-op: git leaves every existing commit exactly as it is instead of replaying it, so that earlier
unsigned commit stays unsigned even though the job reports a signed HEAD. The job always force-rebases
(`-S -f`) so the range is replayed — and re-signed — every time, and it verifies every commit about to
be pushed (not just HEAD) before pushing at all, refusing (parked, `UNSIGNED <sha>`) rather than land one
with no signature or a bad one (`%G?` N or B; U and E carry a signature this host cannot fully trust or
check, and the drain reports them).

## Folding fixes into an unsigned job — `--supersede`

A review round (fix → enqueue → wait for `make sign` → reply) that gets another round of fixes before
the owner drains would otherwise mean two jobs for one topic — or a hand check of the log/`.failed`
state before deleting the old `.sh` yourself. `--supersede` does this in one step: it finds the newest
PENDING job with the same `topic` (and, when you also pass `--by`, the same `--by`), deletes it, prints
which job it dropped, then enqueues as usual. It refuses (exit 2, nothing touched) when that job already
has a drain log or is parked as `<job>.failed` — it was attempted, not just sitting in the queue, and
folding into it would lose that history; resolve it (`make sign_show` / `sign_log` / `sign_retry`) first
instead. No match for the topic (and `--by`) is not an error: the job just enqueues normally, same as
without the flag.

**Folding does not merge the dropped job's own content** — its `--files`, message file and
`--rebase`/`--onto`/`--force-with-lease` flags are gone with it, not carried onto the new enqueue. Pass
the union of both rounds' `--files` (and whichever of those flags the earlier round needed) on the new
call, or the earlier round's paths are left uncommitted and never pushed.

## Legacy queue migration

Jobs a pre-workspace-queue kit left under the kit dir move under that same lock, only into the queue of the
workspace the kit sits in (a kit draining another workspace's queue leaves them alone and names them on
stderr; a plugin install sits in no workspace, so the workspace it drains takes them): `run` moves them
before loading a job (`make sign` calls `run` only; `--dry-run` moves nothing), and `make sign_list` runs
`signq.py migrate-legacy` first, taking the lock for the move; a drain already holding it blocks the move
and says so.

## A killed drain

A job stays in the drain's terminal session — signing may need the terminal for a passphrase, and Ctrl-C
stops the drain and its job together. A `kill -9`'d drain can still leave its job running, so the lock file
records the holder's pid and job. The next `run` that finds that process alive (same pid and start time via
`ps -o lstart=` in UTC, so neither a reused pid nor a zombie counts) names the job and pid and exits 2
without starting one; once the process is gone, `run` drains as usual. An exit 2 for a pid `ps -p <pid>`
shows is not that job: delete `<queue>/.lock` while no drain runs, then `make sign` again. Not covered: two
machines sharing one queue directory.
