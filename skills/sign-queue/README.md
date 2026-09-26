# sign-queue

A shared queue for commits that must be GPG- or SSH-signed and pushed by the user on the host: a session
enqueues a job (worktree, branch, message file, flags); the user drains the queue with one command.

**Needs:** `systems.signed_commits` (the repos enforce a signed-commits ruleset and the editing machine cannot
sign). No env fact rows; the queue itself is the kit-root `sign-queue/` directory (ignored by git, only `.gitkeep` tracked).

**Without it:** inert — where `systems.signed_commits` is false a commit is made and pushed the ordinary way,
and the skill is never invoked.

**Example:**

> **claude:** stages exactly the intended files in the worktree, writes the message to a file and runs
> `sh $BATON/skills/sign-queue/enqueue.sh …` → `queued job 3: <branch> (2 files)`.
>
> **user (on the host):** `make sign` → the job is signed, pushed and removed from the queue.
