# sign-queue

A shared queue for commits that must be GPG- or SSH-signed and pushed by the user on the host: a session
enqueues a job (worktree, branch, message file, flags); the user drains the queue with one command.

**Needs:** `systems.signed_commits` (the repos enforce a signed-commits ruleset and the editing machine cannot
sign). No env fact rows; the queue itself is `<workspace>/.context/state/sign-queue/` — in the workspace, never
under the kit, so a plugin update or kit re-clone never deletes it. Nothing ships or seeds that directory: the
first `enqueue.sh` call creates it.

**Without it:** inert — where `systems.signed_commits` is false a commit is made and pushed the ordinary way,
and the skill is never invoked. The host-side `make sign*` targets carry the same gate: they print one
"not applicable here" line and exit 0 instead of touching the queue.

**Example:**

> **claude:** stages exactly the intended files in the worktree, writes the message to a file and runs
> `sh $BATON/skills/sign-queue/enqueue.sh … --files "path/one path/two"` → `queued topic: KEY-123 · epic ? · repo #4 · <subject>`.
>
> **user (on the host):** `make sign` → the job is signed, pushed and removed from the queue.
