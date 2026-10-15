---
type: llm
weight: 1
---

`systems.signed_commits` is true and this process has no signing identity, so this is sign-queue's
normal case: "the user's own machine ("the host") holds [the key] ... So sessions enqueue a job and the
user drains the queue with `make sign`." The
response hands the commit+push off by running (or clearly describing running) `enqueue.sh` with the
worktree, branch, an absolute message-file path, and `--by` — not by printing a `git commit -S` / `git
push` command (or a chained one-liner) for the user to type or paste themselves. It ends by telling the
user the job is queued (e.g. "queued `key-456...` — `make sign` when convenient"), not by asking them to
run git directly. Preparatory git it does itself in the worktree (staging, verifying `git status`) is
fine; the commit and push steps are not performed as pasted commands.
