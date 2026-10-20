# sign-queue — the drain, on the host

Loaded on demand from `SKILL.md` § User side. Every `make sign*` target wraps `$BATON/skills/sign-queue/signq.py`
(stdlib python3) and is a no-op line where `signed_commits` is false.

## What a drain prints

The **overview table** (`# · TICKET · EPIC · REPO · PR · BRANCH · COMMIT · STAGE · BY`), one **card per job**
(ticket · epic title · repo #PR, the commit subject, branch and the step chain `stage → commit -S → [rebase] →
push`), the milestones as they happen (`committed <sha>`, `rebased`, `pushed a..b`), then `✔ pushed <sha> signed ✓`
or `✘ FAILED … → parked` with a diagnosis hint and the last output lines, and finally a **summary** with one line
per job and its PR link. Git runs with `GIT_PAGER=cat`, `GIT_EDITOR=true`, `GIT_SEQUENCE_EDITOR=true`,
`GIT_TERMINAL_PROMPT=0`, so nothing waits for a `q` or `:wq`. Full per-job output:
`.context/state/sign-queue/logs/<job>.log`.

## Jobs

Plain sh files under `.context/state/sign-queue/` — in the workspace, so a plugin update never deletes them;
the first `enqueue.sh` creates the directory. Each runs: stage → `commit -S -F <msg>` (skipped when nothing is
staged, so a push-only retry works) → optional `fetch` + `rebase -S -f` → verify every commit about to be pushed →
`push origin HEAD:refs/heads/<branch>` → confirm origin's branch is at HEAD (else the job exits 1 with `PUSH NOT
LANDED`, so a push that moved nothing never reads as pushed). Success deletes the job. A failure parks it as `<job>.failed`, the drain continues with the next job, the
summary names the failed topics and their owning sessions, and the exit status is 1.

## One drain at a time

`make sign` holds an exclusive lock on the queue for the whole run; a second one prints the holder ("another
drain (pid 1234, on key-123-p4) already holds the lock — exiting", or "another sign-queue process") and exits 0.
A job file removed by hand during a drain is skipped with one line.

A job stays in the drain's terminal session — signing may need it for a passphrase, and Ctrl-C stops the drain
and its job together. A drain killed outright (`kill -9`) can leave its job running, so the lock file records
the holder's pid and job; the next `run` that finds that process alive (same pid and start time by `ps -o
lstart=`, read in UTC) names it and exits 2. An exit 2 for a pid `ps -p <pid>` shows is not that job: delete
`<queue>/.lock` while no drain runs, then `make sign` again. Not covered: two machines sharing one queue directory.

## Legacy jobs

Jobs a pre-workspace-queue kit left under the kit dir are moved under the same lock, only into the queue of the
workspace the kit sits in (a plugin install sits in none, so the workspace it drains takes them): `run` moves
them before it loads a job (`--dry-run` moves nothing), and `make sign_list` runs `signq.py migrate-legacy`
first; while a drain holds the lock it moves nothing and says so.

## `sign.sh`

`sign.sh` beside the skill is kept as a compatibility shim (`exec python3 signq.py "$@"`) for host aliases that
predate `make sign`; nothing in the kit calls it, and prose names `make sign` only.
