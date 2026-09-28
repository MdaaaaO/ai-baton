---
type: llm
weight: 1
---

The response verifies staged files, writes the message to a file outside the worktree, and enqueues the job, then tells the user one line to drain it. It does not paste a `git commit`/`git push` command for the user to run. The run starts in an empty scratch directory with no live workspace, tracker, chat or git access, so judge the approach, not whether an external call succeeded.
